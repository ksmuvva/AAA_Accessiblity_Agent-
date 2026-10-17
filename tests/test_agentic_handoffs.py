"""G2: SDK handoffs between the lead and its subagents (specialists, explorer, verifier), driven by the scripted
RouterModel. Real Runner, real tools, real pages; no API calls."""

import asyncio
import json
from pathlib import Path

import pytest
from agents import RunContextWrapper, Runner
from test_agent import RouterModel, call

from agent_access.agent_tools.browser_tools import load_html_content
from agent_access.context import AuditContext
from agent_access.engine import run_sweep
from agent_access.team import build_audit_agent, build_team, final_notes, lead_must_resume
from agent_access.team.handoffs import HANDBACK, brief_section, handoff_log
from agent_access.team.runtime import run_config

FIXTURES = Path(__file__).parent / "fixtures"
PAGE = "file:bad_page.html"
SPECIALIST_HANDOFFS = {"audit_forms", "audit_media", "audit_navigation"}
SUBAGENT_HANDOFFS = {"explore_page", "verify_judgement"}


async def _team_run(model, scripts_page_html: str, lead_prompt: str, *, allow_interaction=False, sweep=True, max_turns=25):
    async with AuditContext(screen_reader="emulated", allow_interaction=allow_interaction) as audit:
        await load_html_content(audit, scripts_page_html, PAGE)
        if sweep:
            await run_sweep(audit)
        team = build_team(model, audit)
        result = await Runner.run(team.lead, lead_prompt, context=audit, max_turns=max_turns,
                                  run_config=run_config(audit, "test: handoffs"))
        return audit, team, result


def bad_page() -> str:
    return (FIXTURES / "bad_page.html").read_text(encoding="utf-8")


def test_build_team_wires_handoffs_both_ways():
    team = build_team("m")
    assert team.delegation == "handoffs" and team.lead.name == "Lead auditor"
    with pytest.raises(ValueError):
        build_team("m", delegation="handoff")  # one spelling, the CLI's and AuditContext.delegation's
    assert {h.tool_name for h in team.lead.handoffs} == SPECIALIST_HANDOFFS | SUBAGENT_HANDOFFS
    assert not [t for t in team.lead.tools if t.name in SPECIALIST_HANDOFFS]  # specialists are handoffs, not tools
    assert "return_to_page" in {t.name for t in team.lead.tools}
    for agent in (*team.specialists.values(), team.explorer, team.verifier):
        assert [h.tool_name for h in agent.handoffs] == [HANDBACK] and agent.handoffs[0].agent_name == "Lead auditor"
    assert set(team.by_name) == {"Lead auditor", "Forms specialist", "Media specialist", "Navigation specialist",
                                 "Explorer", "Verifier", "Reviewer", "Summary writer"}
    assert team.reviewer is team.by_name["Reviewer"] and team.summarizer.name == "Summary writer"

    # Tools mode: no conversation transfers at all; the specialists, explorer and verifier are nested tools.
    tools_team = build_team("m", delegation="tools")
    assert tools_team.lead.handoffs == [] and not tools_team.explorer.handoffs
    assert SPECIALIST_HANDOFFS | SUBAGENT_HANDOFFS <= {t.name for t in tools_team.lead.tools}

    # Code mode's lead is unchanged: delegate tools, no subagents, same tool budget.
    legacy = build_audit_agent("m")
    assert legacy.handoffs == [] and len(legacy.tools) <= 19 and SPECIALIST_HANDOFFS <= {t.name for t in legacy.tools}

    # The verifier judges any criterion; the explorer can browse.
    verifier_enum = next(t for t in team.verifier.tools if t.name == "get_criterion").params_json_schema["properties"]["criterion"]["enum"]
    assert len(verifier_enum) == 86 and {"record_finding", "verify_finding", "run_probe"} <= {t.name for t in team.verifier.tools}
    assert {"click_element", "label_page_state", "discover_links"} <= {t.name for t in team.explorer.tools}


def test_specialist_handoff_round_trip():
    model = RouterModel({
        "lead auditor": [
            call("get_open_items", {}),
            call("take_screenshot", {"full_page": False}),
            call("audit_forms", {"focus": "the sign-up form"}),
            # Back from the specialist: the lead judges something itself and finishes.
            call("record_finding", {"criterion": "2.4.2", "status": "pass", "description": "Title names the page.",
                                    "evidence": "Title is 'Bad page'.", "selectors": ["title"]}),
            {"notes_for_human": ["done"]},
        ],
        "forms specialist": [
            call("get_open_items", {}),
            call("record_finding", {"criterion": "3.3.2", "status": "fail", "description": "Email input has no label.",
                                    "evidence": "list_forms: label '' and visible_label false.", "selectors": ["input[name=email]"]}),
            call(HANDBACK, {"summary": "One form tested.", "criteria_recorded": ["3.3.2"], "unresolved": []}),
        ],
    })
    audit, team, result = asyncio.run(_team_run(model, bad_page(), f"Audit this page: {PAGE}"))
    assert all(not steps for steps in model.scripts.values())
    assert result.last_agent is team.lead and not lead_must_resume(result, team)
    assert final_notes(result) == ["done"]
    assert "audit_forms" in audit.attempted_on(PAGE)

    log = handoff_log(audit)
    assert [(e["from"], e["to"]) for e in log] == [("Lead auditor", "Forms specialist"), ("Forms specialist", "Lead auditor")]
    assert log[0]["tool"] == "audit_forms" and log[0]["input"] == {"focus": "the sign-up form"}
    assert log[1]["report"]["criteria_recorded"] == ["3.3.2"] and any(c.startswith("3.3.1") for c in log[1]["not_judged"])

    # The specialist's history holds only messages: no tool calls, outputs, screenshots, handoff or reasoning items
    # (a reasoning model rejects a function_call sent back without its reasoning item). The brief is in its instructions.
    specialist_input = model.inputs["forms specialist"][0]
    assert {i.get("type", "message") for i in specialist_input} == {"message"} and f"Audit this page: {PAGE}" in json.dumps(specialist_input)
    assert "data:image/jpeg" not in json.dumps(specialist_input)
    forms = team.specialists["forms"]
    assert forms.instructions(RunContextWrapper(context=audit), forms).startswith("You are the forms specialist")
    assert brief_section(audit, forms.name) == ""  # handed back: no active brief any more
    audit.handoffs = log[:1]  # as the specialist saw it while working
    assert "the sign-up form" in forms.instructions(RunContextWrapper(context=audit), forms)
    audit.handoffs = log
    # The lead, back in control, saw the specialist's report.
    assert HANDBACK in json.dumps(model.inputs["lead auditor"][-1])

    statuses = {(f.wcag_criterion, f.status.value) for f in audit.findings if f.source == "agent"}
    assert {("3.3.2", "fail"), ("2.4.2", "pass")} <= statuses


def test_missed_handback_is_detected_and_the_lead_resumes():
    model = RouterModel({
        "lead auditor": [call("audit_media", {"focus": ""}), {"notes_for_human": ["resumed"]}],
        # Ends the run with its own report instead of handing back.
        "media specialist": [{"summary": "No media.", "criteria_recorded": [], "unresolved": ["Could not play the video."]}],
    })

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            await load_html_content(audit, bad_page(), PAGE)
            await run_sweep(audit)
            team = build_team(model, audit)
            first = await Runner.run(team.lead, f"Audit this page: {PAGE}", context=audit, max_turns=10,
                                     run_config=run_config(audit, "test"))
            resumed = None
            if lead_must_resume(first, team):
                resumed = await Runner.run(team.lead, first.to_input_list(), context=audit, max_turns=10,
                                           run_config=run_config(audit, "test"))
            return team, first, resumed

    team, first, resumed = asyncio.run(scenario())
    assert first.last_agent is team.specialists["media"] and lead_must_resume(first, team)
    assert final_notes(first) == ["Media specialist: Could not play the video."]
    assert resumed is not None and resumed.last_agent is team.lead and final_notes(resumed) == ["resumed"]


MENU_PAGE = """<html lang="en"><head><title>Menu page</title></head><body><main><h1>Menu page</h1>
<button id="menu-button" aria-expanded="false" onclick="document.getElementById('menu').hidden=false;this.setAttribute('aria-expanded','true')">Menu</button>
<ul id="menu" hidden><li><a href="/a">A</a></li></ul></main></body></html>"""


def test_explorer_labels_states_and_the_lead_returns_to_the_page():
    report = {"states": [{"label": "menu-open", "how_reached": "clicked #menu-button", "what_changed": "menu shown"}],
              "flows": [], "links_worth_auditing": [], "blocked": []}
    model = RouterModel({
        "lead auditor": [call("explore_page", {"goal": "open the menu"}), call("get_open_items", {}),
                         call("return_to_page", {}), {"notes_for_human": []}],
        "explorer": [call("click_element", {"selector": "#menu-button"}), call("label_page_state", {"label": "menu-open"}),
                     call(HANDBACK, report)],
    })
    audit, team, result = asyncio.run(_team_run(model, MENU_PAGE, f"Audit this page: {PAGE}", allow_interaction=True))
    assert all(not steps for steps in model.scripts.values()) and result.last_agent is team.lead
    state = f"{PAGE} [menu-open]"
    assert state in audit.pages and state in audit.swept
    # The lead judged the state while it was loaded, then went back to the base page.
    items = json.loads(next(i["output"] for i in reversed(model.inputs["lead auditor"][2])
                            if i.get("type") == "function_call_output"))  # the newest output: get_open_items
    assert items["page_url"] == state
    assert audit.current_url == PAGE and PAGE not in audit.dirty
    assert "explore_page" in audit.attempted_on(PAGE)
    assert handoff_log(audit)[-1]["report"] == report


def test_brief_survives_a_page_key_change_and_ends_at_handback():
    """Dynamic instructions are rebuilt on every model call: the brief must not vanish when label_page_state moves
    the page key, and must vanish once the subagent has handed back."""
    audit = AuditContext()
    audit.current_url = "page"
    assert brief_section(audit, "Explorer") == ""
    handoff_log(audit).append({"from": "Lead auditor", "to": "Explorer", "page": "page", "owner": None,
                               "tool": "explore_page", "input": {"goal": "open the menu"}})
    audit.current_url = "page [menu-open]"
    assert "open the menu" in brief_section(audit, "Explorer") and brief_section(audit, "Verifier") == ""
    handoff_log(audit).append({"from": "Explorer", "to": "Lead auditor", "page": audit.current_url, "owner": None,
                               "tool": HANDBACK, "report": {}})
    assert brief_section(audit, "Explorer") == ""
    # A fork (G3) with another owner doesn't see this context's briefs.
    audit.owner = "fork-1"
    handoff_log(audit).append({"from": "Lead auditor", "to": "Explorer", "page": "page", "owner": None,
                               "tool": "explore_page", "input": {"goal": "root's goal"}})
    assert brief_section(audit, "Explorer") == ""


def test_verifier_checks_and_records_a_judgement_before_the_lead_moves_on():
    model = RouterModel({
        "lead auditor": [
            call("verify_judgement", {"criterion": "1.1.1", "status": "fail", "description": "Logo alt is a file name.",
                                      "evidence": "Alt text looks like 'logo.png'.", "selectors": ["img"]}),
            {"notes_for_human": []},
        ],
        "verifier": [
            call("get_criterion", {"criterion": "1.1.1"}),
            call("inspect_element", {"selector": "img"}),
            call("record_finding", {"criterion": "1.1.1", "status": "fail", "description": "Logo alt is a file name.",
                                    "evidence": "inspect_element: alt is a file name.", "selectors": ["img"]}),
            call(HANDBACK, {"criterion": "1.1.1", "verdict": "confirmed", "reason": "Seen in inspect_element."}),
        ],
    })
    audit, team, result = asyncio.run(_team_run(model, bad_page(), f"Audit this page: {PAGE}"))
    assert all(not steps for steps in model.scripts.values()) and result.last_agent is team.lead
    assert "verify_judgement" in audit.attempted_on(PAGE)
    verified = [f for f in audit.findings if f.source == "agent" and f.wcag_criterion == "1.1.1"]
    assert len(verified) == 1 and verified[0].status.value == "fail" and "inspect_element" in verified[0].evidence
    assert handoff_log(audit)[-1]["report"]["verdict"] == "confirmed"
    assert "record_findings" not in model.tools_seen["verifier"] and "get_criterion" in model.tools_seen["verifier"]
