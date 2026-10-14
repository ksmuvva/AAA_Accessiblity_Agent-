"""G7 integration: the agentic team end to end with scripted models (no OpenAI calls).

The contract these tests hold the other lanes to: agent mode may change who decides the order and the extras, but
every target criterion on every audited page still ends measured and decided or agent-judged, guardrail tripwires are
recorded instead of obeyed, and a run that stops early reports what it skipped. Tests skip until the lane interface
they exercise exists, so this file imports cleanly while the lanes land.
"""

import asyncio
import inspect
import json
from pathlib import Path

import pytest

from agent_access.agent_tools.browser_tools import load_html_content
from agent_access.context import AuditContext
from agent_access.team.orchestrator import run_audit
from test_agent import RouterModel, call, confirm_all, judge_everything_open, team_scripts, SUMMARY  # noqa: F401

FIXTURES = Path(__file__).parent / "fixtures"
PAGE = "file:bad_page.html"
CASES = Path(__file__).parent.parent / "evals" / "cases"


def agent_mode_available() -> bool:
    return "mode" in inspect.signature(run_audit).parameters


needs_agent_mode = pytest.mark.skipif(not agent_mode_available(), reason="G1's run_audit(mode=...) hasn't landed")


def planner_scripts(page: str, lead_steps: list, extra: dict | None = None) -> dict:
    """Scripts for the planner-led team: the planner audits the loaded page, then finishes; the lead judges.
    Tool names and the PlanReport shape are G1's (see tests/test_agentic_planner.py)."""
    scripts = team_scripts()
    scripts["lead auditor"] = lead_steps
    scripts["reviewer"] = [confirm_all] * 6
    scripts["audit planner"] = [
        call("get_plan_status", {}),
        call("audit_pages", {"page_keys": [page], "focus": ""}),
        call("finish", {"reason": "done"}),
        {"pages_audited": [page], "goal_changes": [], "stopped_because": "done", "notes_for_human": []},
    ]
    scripts.update(extra or {})
    return scripts


def run(model, html: str, label: str, **kwargs):
    async def scenario():
        async with AuditContext(output_dir=kwargs.pop("tmp_path"), screen_reader="emulated") as audit:
            await load_html_content(audit, html, label)
            outcome = await run_audit(audit, model=model, max_turns=12, progress=lambda m: None, **kwargs)
            return audit, outcome

    return asyncio.run(scenario())


@needs_agent_mode
def test_agent_mode_keeps_the_coverage_floor(tmp_path):
    """Lazy measurement + planner: at the end every AA criterion is measured (a tool run exists for its rule) and is
    a confirmed fail or agent-judged, with no coverage warnings."""
    lead = [call("get_open_items", {}), judge_everything_open, call("get_open_items", {}), judge_everything_open,
            call("get_open_items", {}), {"notes_for_human": []}]
    model = RouterModel(planner_scripts(PAGE, lead))
    audit, outcome = run(model, (FIXTURES / "bad_page.html").read_text(encoding="utf-8"), "file:bad_page.html",
                         tmp_path=tmp_path, mode="agent")
    assert outcome.errors == [], outcome.errors
    page = audit.pages[0]
    measured = {r["criteria"] for r in audit.tool_runs if r["page_url"] == page}
    target = {r.wcag_criterion for r in audit.target_rules()}
    assert target <= measured, sorted(target - measured)
    judged = {f.wcag_criterion for f in audit.findings if f.source == "agent"}
    failed = {c.wcag_criterion for c in outcome.report.criteria if c.status.value == "fail"}
    assert judged | failed == target, sorted(target - (judged | failed))
    assert outcome.report.counts["not_tested"] == 0 and outcome.report.counts["needs_review"] == 0
    assert not [w for w in outcome.warnings if "still open" in w]


@needs_agent_mode
def test_lead_that_stops_early_still_leaves_a_fully_measured_page(tmp_path):
    """The floor sweep runs whatever the lead never asked for; the warning names what was skipped."""
    lead = [call("get_open_items", {}), {"notes_for_human": []}] * 3
    model = RouterModel(planner_scripts(PAGE, lead))
    audit, outcome = run(model, (FIXTURES / "bad_page.html").read_text(encoding="utf-8"), "file:bad_page.html",
                         tmp_path=tmp_path, mode="agent")
    page = audit.pages[0]
    measured = {r["criteria"] for r in audit.tool_runs if r["page_url"] == page}
    assert {r.wcag_criterion for r in audit.target_rules()} <= measured
    assert outcome.report.counts["not_tested"] == 0
    assert any("open" in w for w in outcome.warnings)


def test_page_content_injection_is_scrubbed_and_recorded(tmp_path):
    """The injection eval page: the instruction text never reaches the model unscrubbed, a prompt_injection tripwire is
    recorded, and no judgement is recorded from it."""
    pytest.importorskip("agent_access.team.guardrails")
    from agent_access.team.guardrails import REDACTED

    page = "file:injection_bad.html"
    # The outline carries structure only; the accessibility tree carries the page text the injection sits in.
    lead = [call("get_accessibility_tree", {}), call("get_open_items", {}), judge_everything_open,
            call("get_open_items", {}), {"notes_for_human": []}]
    model = RouterModel(planner_scripts(page, lead))
    mode = {"mode": "agent"} if agent_mode_available() else {}
    audit, outcome = run(model, (CASES / "injection_bad.html").read_text(encoding="utf-8"), page, tmp_path=tmp_path, **mode)
    kinds = {t.get("kind") for t in audit.tripwires}
    assert "prompt_injection" in kinds, audit.tripwires
    tree = next(i["output"] for i in model.inputs["lead auditor"][-1] if i.get("type") == "function_call_output")
    assert REDACTED in tree and "ignore all previous instructions" not in tree.lower()
    assert sum(t.get("kind") == "prompt_injection" for t in audit.tripwires) == 1  # one per page, however many tools saw it
    assert {c.wcag_criterion for c in outcome.report.criteria if c.status.value == "fail"} >= {"1.1.1", "2.4.2", "3.3.2"}


@needs_agent_mode
def test_budget_exhaustion_ends_the_run_cleanly(tmp_path):
    pytest.importorskip("agent_access.budget")
    from agent_access.budget import Budget

    lead = [call("get_open_items", {}), judge_everything_open, call("get_open_items", {}), {"notes_for_human": []}]
    model = RouterModel(planner_scripts(PAGE, lead))

    async def scenario():
        async with AuditContext(output_dir=tmp_path, screen_reader="emulated") as audit:
            audit.budget = Budget(max_model_calls=3, reserve_calls=0)
            await load_html_content(audit, (FIXTURES / "good_page.html").read_text(encoding="utf-8"), "file:good_page.html")
            return await run_audit(audit, model=model, max_turns=12, progress=lambda m: None, mode="agent")

    outcome = asyncio.run(scenario())
    assert outcome.report is not None  # whatever was measured is still reported
    assert getattr(outcome, "budget_used", None) is not None
    assert any("budget" in (w + e).lower() for w in outcome.warnings for e in [""]) or any("budget" in e.lower() for e in outcome.errors)
