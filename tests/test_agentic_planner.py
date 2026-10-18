"""G1: the Budget and, once the gate opens, the planner agent and its code floors."""

from agent_access.budget import Budget


def test_working_agents_stop_at_the_soft_limit_but_closing_agents_keep_the_reserve():
    import pytest
    from agent_access.budget import BudgetExhausted, charge_model_call
    from types import SimpleNamespace
    audit = SimpleNamespace(budget=Budget(max_model_calls=5, reserve_calls=2, max_minutes=60))
    for _ in range(3):
        charge_model_call(audit, "Lead auditor")
    with pytest.raises(BudgetExhausted):
        charge_model_call(audit, "Lead auditor")              # the working budget (3) is spent
    charge_model_call(audit, "Reviewer"); charge_model_call(audit, "Summary writer")   # the reserve
    with pytest.raises(BudgetExhausted):
        charge_model_call(audit, "Reviewer")                  # hard cap
    charge_model_call(SimpleNamespace(budget=None), "Lead auditor")   # no budget: no-op


def test_budget_keeps_a_reserve_for_the_closing_stages():
    b = Budget(max_model_calls=10, reserve_calls=3, max_minutes=60)
    for _ in range(7):
        b.spend()
    assert b.exhausted and not b.hard_exhausted          # no new agent work, reviewer/summary may still run
    assert b.remaining()["calls"] == 0 and b.remaining()["reserve_calls"] == 3
    b.spend(3)
    assert b.hard_exhausted


def test_a_later_page_starts_only_when_its_review_fits():
    from agent_access.context import AuditContext
    from agent_access.team.planner import PlanState, run_lead_on

    b = Budget(max_model_calls=120, reserve_calls=60, review_calls_per_page=20)
    assert b.admit_page() and b.reserve_calls == 80 and b.working_calls == 40
    b.spend(30)
    assert not b.admit_page() and b.reserve_calls == 80   # 30 + 20 > 40: no room left to review another page
    audit = AuditContext()
    audit.budget, audit.pages = b, ["https://shop.example/", "https://shop.example/cart"]
    state = PlanState(team=None, model=None, reviewer_model=None, review=True, min_pages=2, max_pages=2,
                      lead_runs={"https://shop.example/": 2})
    refused = asyncio.run(run_lead_on(audit, state, "https://shop.example/cart", ""))
    assert "not started" in refused["error"] and "https://shop.example/cart" in state.exhausted


# ---------------------------------------------------------------- the planner, offline, with scripted models

import asyncio
from pathlib import Path

from test_agent import SUMMARY, RouterModel, call, confirm_all

from agent_access.agent_tools.browser_tools import load_html_content
from agent_access.context import AuditContext
from agent_access.team.planner import run_agentic_audit

FIXTURES = Path(__file__).parent / "fixtures"
PAGE = "file:bad_page.html"


def last_tool_output(model, role, turn):
    """The newest function_call_output in the input of the model's n-th call for `role`."""
    return [i["output"] for i in model.inputs[role][turn] if i.get("type") == "function_call_output"][-1]


def _run(scripts, tmp_path, **kwargs):
    model = RouterModel(scripts)

    async def scenario():
        async with AuditContext(output_dir=tmp_path, screen_reader="emulated", tracing=False) as audit:
            await load_html_content(audit, (FIXTURES / "bad_page.html").read_text(encoding="utf-8"), PAGE)
            if kwargs.pop("lazy", False):       # lazy measurement: only the cheap probes, as run_audit does on load
                from agent_access.agent_tools.check_tools import measure_on_load
                audit.measurement = "lazy"
                await measure_on_load(audit)
            if kwargs.pop("measured", False):   # as run_audit does: the start page is measured before the planner starts
                from agent_access.engine import run_sweep
                await run_sweep(audit)
            if kwargs.pop("explorer_state", False):   # a labelled state with no recorded steps: it can't be rebuilt
                audit.set_current(PAGE + " [after search]")
                audit.current_url = PAGE
            outcome = await run_agentic_audit(audit, model=model, progress=lambda m: None, **kwargs)
            return audit, outcome

    audit, outcome = asyncio.run(scenario())
    return model, audit, outcome


def test_planner_owns_the_run_and_the_code_floors_close_it(tmp_path):
    scripts = {
        "audit planner": [
            call("get_plan_status", {}),
            call("set_goal", {"pages": 1, "level": "AA", "flows": [], "reason": "One page was requested."}),
            call("audit_pages", {"page_keys": [PAGE], "focus": ""}),
            call("finish", {"reason": "done"}),
            {"pages_audited": [PAGE], "goal_changes": ["kept at one page"], "stopped_because": "done", "notes_for_human": []},
        ],
        "lead auditor": [
            call("get_open_items", {}),
            call("record_finding", {"criterion": "2.4.2", "status": "pass", "description": "Descriptive title.",
                                    "evidence": "Title names the page and the site.", "selectors": ["title"]}),
            {"notes_for_human": ["No video to play."]},
            {"notes_for_human": []},   # the continuation run that always follows the first
        ],
        "reviewer": [confirm_all],
        "summary writer": [SUMMARY],
    }
    model, audit, outcome = _run(scripts, tmp_path)
    assert all(not steps for steps in model.scripts.values()), model.scripts
    planner_tools = set(model.tools_seen["audit planner"])
    assert {"get_plan_status", "open_page", "discover_links", "set_goal", "audit_pages", "explore", "run_site_tests_tool",
            "review_pages", "write_summary", "recall", "remember", "finish"} <= planner_tools
    actions = [e["action"] for e in outcome.plan_log]
    assert actions[:3] == ["set_goal", "audit_page", "finish"]            # the planner's own decisions, in order
    assert "review" in actions and "summary" in actions                   # closing stages ran (code floor)
    assert outcome.report is not None and outcome.summary is not None
    assert outcome.budget_used["max_model_calls"] == 400 and isinstance(outcome.tripwires, list)
    assert any(n.startswith(f"{PAGE}: No video") for n in outcome.lead_notes)
    assert outcome.errors == []


def test_finish_is_refused_until_opened_pages_are_audited_then_code_floor_audits_them(tmp_path):
    scripts = {
        "audit planner": [
            call("get_plan_status", {}),
            call("finish", {"reason": "done"}),   # refused: the page was never audited
            *[lambda input: {"pages_audited": [], "goal_changes": [], "stopped_because": "gave up", "notes_for_human": []}] * 3,
        ],   # it gives up, and again each of the two times the harness hands it back what it owes
        "lead auditor": [call("get_open_items", {}), {"notes_for_human": []}, {"notes_for_human": []}],   # + the continuation run that always follows the first
        "reviewer": [confirm_all],
        "summary writer": [SUMMARY],
    }
    model, audit, outcome = _run(scripts, tmp_path)
    refused = last_tool_output(model, "audit planner", 2)
    assert '"ok": false' in refused and PAGE in refused
    actions = [e["action"] for e in outcome.plan_log]
    assert actions.count("planner_resumed") == 2 and "You stopped without finish" in model.inputs["audit planner"][3][-1]["content"]
    assert "lead_floor" in actions and "finish" not in actions
    assert not model.scripts["lead auditor"]        # the code floor ran the lead on the page the planner skipped
    assert outcome.report is not None


def test_explorer_states_do_not_block_finish(tmp_path):
    scripts = {
        "audit planner": [
            call("audit_pages", {"page_keys": [PAGE], "focus": ""}),
            call("finish", {"reason": "done"}),
            {"pages_audited": [PAGE], "goal_changes": [], "stopped_because": "done", "notes_for_human": []},
        ],
        "lead auditor": [{"notes_for_human": []}, {"notes_for_human": []}],   # + the continuation run that always follows the first
        "reviewer": [confirm_all],
        "summary writer": [SUMMARY],
    }
    model, audit, outcome = _run(scripts, tmp_path, explorer_state=True)
    finished = last_tool_output(model, "audit planner", 2)
    assert '"ok": true' in finished    # a labelled state with no recorded steps can't be rebuilt, so it can't block
    assert "lead_floor" not in [e["action"] for e in outcome.plan_log]
    assert PAGE + " [after search]" in audit.pages and outcome.report is not None


def test_agent_mode_lead_has_specialist_handoffs():
    """In agent mode the lead reaches specialists through SDK handoffs named like the planning ids."""
    from agent_access.team.planner import make_team
    audit = AuditContext(screen_reader="emulated", tracing=False)
    audit.delegation = "handoffs"
    team = make_team("m", audit)
    names = {h.tool_name for h in team.lead.handoffs}
    assert {"audit_forms", "audit_media", "audit_navigation"} <= names
    audit.delegation = "tools"
    tools_team = make_team("m", audit)   # specialists as tools (G2: no handoffs at all in this mode)
    assert not {"audit_forms", "audit_media", "audit_navigation"} & {h.tool_name for h in tools_team.lead.handoffs}
    assert {"audit_forms", "audit_media", "audit_navigation"} <= {t.name for t in tools_team.lead.tools}


def test_back_to_checks_the_live_browser_not_only_the_cursor(tmp_path):
    """A measurement floor can reload another page while the cursor stays put; _back_to must notice."""
    from agent_access.team.orchestrator import _back_to

    async def scenario():
        async with AuditContext(output_dir=tmp_path, screen_reader="emulated", tracing=False) as audit:
            await load_html_content(audit, "<html lang='en'><head><title>A</title></head><body><main><h1>A</h1></main></body></html>", "file:a.html")
            await load_html_content(audit, "<html lang='en'><head><title>B</title></head><body><main><h1>B</h1></main></body></html>", "file:b.html")
            audit.current_url = "file:a.html"            # cursor says A, the browser shows B
            ok = await _back_to(audit, "file:a.html")
            return ok, await (await audit.page()).title()

    ok, title = asyncio.run(scenario())
    assert ok and title == "A"


def _skip_scripts(verdict, lead):
    stop = {"pages_audited": [], "goal_changes": [], "stopped_because": "done", "notes_for_human": []}
    return {
        "audit planner": [
            call("skip_pages", {"skips": [{"page": PAGE, "reason": "An error page with nothing to audit."}]}),
            call("finish", {"reason": "done"}),
            # A rejected skip leaves the page owed, so the harness hands the planner back twice before the code floor.
            *([stop] * (3 if verdict == "rejected" else 1)),
        ],
        "skip reviewer": [{"verdicts": [{"page": PAGE, "verdict": verdict, "reason": "Compared the profiles."}]}],
        "lead auditor": lead,
        "reviewer": [confirm_all] * 3,
        "summary writer": [SUMMARY],
    }


def test_an_accepted_skip_lets_the_planner_finish_and_is_reported_with_its_reason(tmp_path):
    model, audit, outcome = _run(_skip_scripts("accepted", lead=[]), tmp_path, measured=True)
    reviewed = last_tool_output(model, "audit planner", 1)
    assert '"verdict": "accepted"' in reviewed
    finished = last_tool_output(model, "audit planner", 2)
    assert '"ok": true' in finished and PAGE in finished           # no hard floor: the justified skip stands
    profile = model.inputs["skip reviewer"][0][-1]["content"]
    assert '"headings": 1' in profile and '"landmarks": [' in profile   # built from the measured page's probe data
    assert '"audited_pages": []' in profile
    actions = [e["action"] for e in outcome.plan_log]
    assert "skip_pages" in actions and "lead_floor" not in actions  # the lead was never forced onto the page
    assert any("skipped by the audit planner (An error page with nothing to audit.)" in w for w in outcome.warnings)


def test_a_rejected_skip_still_gets_its_lead_pass(tmp_path):
    lead = [call("get_open_items", {}), {"notes_for_human": []}, {"notes_for_human": []}]   # + the continuation run that always follows the first
    model, audit, outcome = _run(_skip_scripts("rejected", lead), tmp_path)
    assert '"verdict": "rejected"' in last_tool_output(model, "audit planner", 1)
    refused = last_tool_output(model, "audit planner", 2)
    assert '"ok": false' in refused and "skip_pages" in refused
    assert "lead_floor" in [e["action"] for e in outcome.plan_log]
    assert not model.scripts["lead auditor"]                        # the code floor audited the rejected page
    assert not any("skipped by the audit planner" in w for w in outcome.warnings)


def test_a_skipped_page_that_was_audited_anyway_is_not_reported_as_skipped():
    from agent_access.team.planner import PlanState, accepted_skips

    state = PlanState(team=None, model=None, reviewer_model=None, review=True, min_pages=1, max_pages=1)
    state.skips = {"a": {"reason": "duplicate", "verdict": "accepted"}, "b": {"reason": "error page", "verdict": "accepted"},
                   "c": {"reason": "saves budget", "verdict": "rejected"}}
    state.lead_runs = {"b": 1}
    assert accepted_skips(state) == {"a": "duplicate"}


def _lazy_skip_scripts(planner_steps):
    return {
        "audit planner": planner_steps + [
            {"pages_audited": [], "goal_changes": [], "stopped_because": "done", "notes_for_human": []}],
        "skip reviewer": [{"verdicts": [{"page": PAGE, "verdict": "accepted", "reason": "Compared the profiles."}]}],
        "lead auditor": [],
        "reviewer": [confirm_all] * 6,
        "summary writer": [SUMMARY] * 2,
    }


SKIP = call("skip_pages", {"skips": [{"page": PAGE, "reason": "An error page with nothing to audit."}]})


def test_an_accepted_skip_is_measured_before_the_review_and_summary(tmp_path):
    """Lazy measurement: the skipped page's target checks run when the skip is accepted, so the planner's own
    review and summary already see its findings, and nothing is reviewed or summarised twice."""
    model, audit, outcome = _run(_lazy_skip_scripts([
        SKIP, call("review_pages", {}), call("write_summary", {}), call("finish", {"reason": "done"})]),
        tmp_path, lazy=True)
    fails = [f for f in audit.findings if f.status.value == "fail" or f.original_status is not None]
    assert fails and all(f.review_verdict is not None for f in fails)
    assert len(model.inputs["summary writer"]) == 1


def test_findings_recorded_after_the_review_are_reviewed_and_summarised(tmp_path):
    """If the planner reviews and summarises first and skips afterwards, the late findings are still reviewed and
    the summary is rewritten to include them."""
    model, audit, outcome = _run(_lazy_skip_scripts([
        call("review_pages", {}), call("write_summary", {}), SKIP, call("finish", {"reason": "done"})]),
        tmp_path, lazy=True)
    fails = [f for f in audit.findings if f.status.value == "fail" or f.original_status is not None]
    assert fails and all(f.review_verdict is not None for f in fails)
    assert len(model.inputs["summary writer"]) == 2 and outcome.summary is not None
