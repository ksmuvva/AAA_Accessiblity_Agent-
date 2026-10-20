"""End-to-end team run with scripted fake models: real Runner, real tools, real orchestration, no API calls.

A RouterModel gives each agent (lead, specialists, reviewer, summary writer) its own script, chosen
from the first line of that agent's instructions.
"""

import asyncio
import json
from pathlib import Path

from agents import ModelResponse, Usage
from agents.models.interface import Model
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

from agent_access.team.orchestrator import run_audit
from agent_access.cli import parse_args
from agent_access.report_writer import write_reports
from agent_access.agent_tools.browser_tools import load_html_content
from agent_access.context import AuditContext

FIXTURES = Path(__file__).parent / "fixtures"

SUMMARY = {
    "overall_assessment": "The page has several WCAG 2.2 AA failures.",
    "top_issues": [{"wcag_criterion": "1.1.1", "issue": "Image has no alt text.", "fix": "Add alt text.", "priority": "high"}],
    "manual_review_needed": ["Check reading order with a screen reader."],
}


def call(name, args):
    return ("call", name, args)


class RouterModel(Model):
    """Serves scripted steps per agent role. A step is a tool call, a final dict, or a callable(input) -> step."""

    def __init__(self, scripts: dict[str, list]):
        self.scripts = {role: list(steps) for role, steps in scripts.items()}
        self.tools_seen: dict[str, list[str]] = {}
        self.inputs: dict[str, list] = {}
        self.n = 0

    async def get_response(self, system_instructions, input, model_settings, tools, output_schema, handoffs,
                           tracing, *, previous_response_id=None, conversation_id=None, prompt=None):
        role = next(r for r in self.scripts if system_instructions.startswith(f"You are the {r}"))
        self.tools_seen[role] = [t.name for t in tools]
        self.inputs.setdefault(role, []).append(input)
        step = self.scripts[role].pop(0)
        if callable(step):
            step = step(input)
        self.n += 1
        if isinstance(step, tuple):
            _, name, args = step
            item = ResponseFunctionToolCall(id=f"fc_{self.n}", call_id=f"call_{self.n}", type="function_call",
                                            name=name, arguments=json.dumps(args), status="completed")
        else:
            item = ResponseOutputMessage(id=f"msg_{self.n}", type="message", role="assistant", status="completed",
                                         content=[ResponseOutputText(type="output_text", text=json.dumps(step), annotations=[])])
        return ModelResponse(output=[item], usage=Usage(), response_id=None)

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError


def first_user_text(input) -> str:
    return input if isinstance(input, str) else next(i["content"] for i in input if i.get("role") == "user")


def review_step(input, reject: str | None = "3.1.2"):
    """Reviewer step: reject results for the `reject` criterion (the lead's false 3.1.2 failure), confirm the rest."""
    items = json.loads(first_user_text(input).split("Results to verify:\n", 1)[1])
    return {"verdicts": [
        {"finding_id": i["finding_id"],
         "verdict": "rejected" if reject and i["criterion"].startswith(reject) else "confirmed",
         "reason": "No other-language passage exists." if reject and i["criterion"].startswith(reject) else "Seen on the page."}
        for i in items
    ]}


def confirm_all(input):
    return review_step(input, reject=None)


def team_scripts():
    return {
        "lead auditor": [
            call("get_criterion", {"criterion": "1.1.1"}),
            call("get_open_items", {}),
            call("audit_forms", {"focus": ""}),
            call("take_screenshot", {"full_page": False}),
            # A deliberately false failure the reviewer should catch.
            call("record_finding", {"criterion": "3.1.2", "status": "fail", "description": "French passage without lang.",
                                    "evidence": "Assumed from the accessibility tree.", "selectors": ["p"]}),
            {"notes_for_human": ["No video on the page to play."]},
            # The page still has open items, so the orchestrator gives the lead one fresh continuation run.
            {"notes_for_human": []},
        ],
        "forms specialist": [
            call("list_forms", {}),
            call("test_forms", {"form_selectors": ["form"]}),
            call("record_finding", {"criterion": "3.3.2", "status": "fail", "description": "Email input has no label.",
                                    "evidence": "list_forms: label '' and visible_label false.", "selectors": ["input[name=email]"]}),
            {"summary": "One form tested.", "criteria_recorded": ["3.3.1", "3.3.2"], "unresolved": []},
        ],
        "reviewer": [call("inspect_element", {"selector": "p"}), review_step],
        "summary writer": [SUMMARY],
    }


def test_full_team_run(tmp_path):
    model = RouterModel(team_scripts())

    async def scenario():
        async with AuditContext(output_dir=tmp_path, screen_reader="emulated") as audit:
            await load_html_content(audit, (FIXTURES / "bad_page.html").read_text(encoding="utf-8"), "file:bad_page.html")
            outcome = await run_audit(audit, model=model, max_turns=20, progress=lambda m: None)
            return audit, outcome

    audit, outcome = asyncio.run(scenario())
    assert outcome.errors == []
    assert all(not steps for steps in model.scripts.values())  # every agent ran its whole script

    # Tool sets per role.
    assert "audit_forms" in model.tools_seen["lead auditor"] and "click_element" not in model.tools_seen["lead auditor"]
    # Criteria are reached through two generic tools, not one tool per criterion.
    for role in ("lead auditor", "forms specialist"):
        assert {"get_criterion", "run_checks"} <= set(model.tools_seen[role])
        assert not [t for t in model.tools_seen[role] if t.startswith("check_")], role
    assert {"test_forms", "get_open_items", "record_findings"} <= set(model.tools_seen["forms specialist"])
    assert len(model.tools_seen["lead auditor"]) <= 17
    # The lead read 1.1.1 through get_criterion: status, the check's findings, and what to judge.
    lead_view = json.loads(next(i["output"] for i in model.inputs["lead auditor"][1] if i.get("type") == "function_call_output"))
    assert lead_view["status"] == "fail" and lead_view["check"] == "check_non_text_content" and lead_view["findings"]
    assert lead_view["your_judgement_needed"]
    assert "record_finding" not in model.tools_seen["reviewer"]
    assert "audit_forms" in audit.attempted_on("file:bad_page.html")

    # The screenshot reached the lead as an image.
    assert "data:image/jpeg;base64," in json.dumps(model.inputs["lead auditor"][-2])
    assert "Continue auditing" in first_user_text(model.inputs["lead auditor"][-1])
    # Pages are loaded in code, not by the lead.
    assert not {"open_page", "load_html", "discover_links"} & set(model.tools_seen["lead auditor"])

    # Reviewer: the false 3.1.2 failure is downgraded, real failures stay.
    by = {c.wcag_criterion: c for c in outcome.report.criteria}
    assert by["3.1.2"].status.value == "needs_review"
    lead_312 = next(f for f in by["3.1.2"].findings if f.source == "agent")
    assert lead_312.review_verdict == "rejected" and lead_312.status.value == "needs_review"
    assert by["1.1.1"].status.value == "fail"
    assert all(f.review_verdict == "confirmed" for f in by["1.1.1"].findings if f.status.value == "fail")
    assert by["3.3.2"].status.value == "fail"  # recorded by the forms specialist, confirmed
    assert {r["verdict"] for r in outcome.review_log} == {"confirmed", "rejected"}
    # The forms specialist judged only 3.3.2, so the rest of its criteria are reported as a coverage gap,
    # and so is the page itself, which still has open items after the continuation run.
    assert any("forms specialist left" in w for w in outcome.warnings)
    assert any("stopped with" in w for w in outcome.warnings)

    # The summary writer saw the reviewed data, including the rejection.
    summary_input = json.loads(first_user_text(model.inputs["summary writer"][0]))
    assert summary_input["failures_rejected_by_reviewer"][0]["criterion"] == "3.1.2"
    assert "3.1.2" not in json.dumps(summary_input["confirmed_failures"])
    assert outcome.summary.top_issues[0].wcag_criterion == "1.1.1"
    assert outcome.lead_notes == ["No video on the page to play."]

    json_path, md_path, acr_path = write_reports(tmp_path, outcome)
    md = md_path.read_text(encoding="utf-8")
    assert "Reviewer (rejected)" in md and "Reviewer (confirmed)" in md and "## Auditor notes" in md
    assert "## Coverage warnings" in md and "failure(s) re-checked" in md
    assert json.loads(json_path.read_text(encoding="utf-8"))["review_log"]
    # The confirmed failure fails on the one audited page.
    assert "1.1.1 Non-text Content (Level A)</th><td>Does Not Support</td>" in acr_path.read_text(encoding="utf-8")


def judge_everything_open(input):
    """Lead step: read the get_open_items output just returned and record a judgement for every open criterion."""
    items = json.loads(next(i["output"] for i in reversed(input) if i.get("type") == "function_call_output"))
    return call("record_findings", {"judgements": [
        {"criterion": i["criterion"], "status": "not_applicable" if i["status"] == "not_applicable" else "pass",
         "description": f"Checked {i['title']}.", "evidence": "Scripted test judgement.", "selectors": []}
        for i in items["open"]]})


def test_every_criterion_ends_with_an_agent_judgement(tmp_path):
    """The coverage contract end to end: when the lead judges everything get_open_items lists, no criterion at the
    target level is left to automation alone, and the page closes without coverage warnings."""
    scripts = team_scripts()
    scripts["lead auditor"] = [call("get_open_items", {}), judge_everything_open, call("get_open_items", {}),
                               {"notes_for_human": []}]
    scripts["reviewer"] = [confirm_all] * 4
    model = RouterModel(scripts)

    async def scenario():
        async with AuditContext(output_dir=tmp_path, screen_reader="emulated") as audit:
            await load_html_content(audit, (FIXTURES / "bad_page.html").read_text(encoding="utf-8"), "file:bad_page.html")
            outcome = await run_audit(audit, model=model, max_turns=10, progress=lambda m: None)
            return audit, outcome

    audit, outcome = asyncio.run(scenario())
    assert outcome.errors == [] and outcome.warnings == []
    assert not model.scripts["lead auditor"] and len(model.inputs["lead auditor"]) == 4  # one run, no continuation
    judged = {f.wcag_criterion for f in audit.findings if f.source == "agent"}
    failed = {c.wcag_criterion for c in outcome.report.criteria if c.status.value == "fail"}
    target = {r.wcag_criterion for r in audit.target_rules()}
    assert judged | failed == target and failed  # every AA criterion: judged by the agent, or a confirmed failure
    assert outcome.report.counts["needs_review"] == 0 and outcome.report.counts["not_tested"] == 0
    # Passes that confirm a measured check carry the automated evidence for the reviewer.
    review_input = first_user_text(model.inputs["reviewer"][0])
    assert '"automated_check"' in review_input and '"measured_only": true' in review_input


def test_lead_stopping_early_still_reports(tmp_path):
    """If the lead runs out of turns, recorded failures are still reviewed and reported."""
    scripts = team_scripts()
    scripts["lead auditor"] = [call("run_checks", {"criteria": ["2.4.2"]})] + [call("get_open_items", {})] * 5
    scripts["reviewer"] = [review_step]
    model = RouterModel(scripts)

    async def scenario():
        async with AuditContext(output_dir=tmp_path, screen_reader="emulated") as audit:
            await load_html_content(audit, (FIXTURES / "bad_page.html").read_text(encoding="utf-8"))
            return await run_audit(audit, model=model, max_turns=3, progress=lambda m: None)

    outcome = asyncio.run(scenario())
    # Both lead runs (first and continuation) hit the cap; each error is recorded and the page is flagged.
    assert len(outcome.errors) == 2 and all("MaxTurnsExceeded" in e for e in outcome.errors)
    assert any("stopped with" in w for w in outcome.warnings)
    assert outcome.review_log and outcome.summary is not None
    assert outcome.report.counts["fail"] > 0


def test_interaction_tools_enabled_on_request():
    model = RouterModel({"lead auditor": [{"notes_for_human": []}] * 2, "reviewer": [review_step] * 3,
                         "summary writer": [SUMMARY]})

    async def scenario():
        async with AuditContext(allow_interaction=True, screen_reader="emulated") as audit:
            await load_html_content(audit, "<html lang='en'><title>x</title><body>hi</body></html>")
            await run_audit(audit, model=model, max_turns=2, progress=lambda m: None)

    asyncio.run(scenario())
    assert {"click_element", "fill_field", "press_key", "label_page_state"} <= set(model.tools_seen["lead auditor"])


def test_cli_argument_validation():
    assert parse_args(["https://example.com"]).pages == 1
    assert parse_args(["https://example.com", "--pages", "3", "--skip-review"]).skip_review
    args = parse_args(["https://example.com", "--level", "AAA", "--screen-reader", "emulated"])
    assert args.level == "AAA" and args.screen_reader == "emulated"
    args = parse_args(["https://x.com", "--allow-interaction", "--allow-submit", "https://x.com/api/",
                       "--reviewer-model", "gpt-x", "--no-trace"])
    assert args.allow_submit == ["https://x.com/api/"] and args.reviewer_model == "gpt-x" and args.no_trace
    args = parse_args(["https://x.com", "--mode", "code", "--measurement", "lazy", "--max-pages", "3", "--budget-calls", "100",
                       "--process", "checkout"])  # interaction is on by default, so --process alone is valid
    assert args.mode == "code" and args.measurement == "lazy" and args.max_pages == 3 and args.process == "checkout"
    assert parse_args(["https://x.com"]).measurement == "lazy"  # the agents choose what to measure by default
    assert parse_args(["https://x.com", "--measurement", "eager"]).measurement == "eager"
    for bad in (["https://x.com", "--process", "checkout", "--no-interaction"], ["--html-file", "a.html", "--pages", "2"], [],
                ["https://x.com", "--allow-submit", "https://x.com/", "--no-interaction"], ["https://x.com", "--max-turns", "0"],
                ["https://x.com", "--measurement", "sometimes"]):
        try:
            parse_args(bad)
        except SystemExit:
            continue
        raise AssertionError(f"accepted invalid args: {bad}")


def test_login_flags(tmp_path):
    state = tmp_path / "session.json"
    state.write_text('{"cookies": [], "origins": []}')
    assert parse_args(["https://x.com/account", "--storage-state", str(state)]).storage_state == state
    assert parse_args(["https://x.com/login", "--save-login", str(state)]).save_login == state
    for bad in (["--html-file", "a.html", "--save-login", str(state)], ["https://x.com", "--storage-state", str(tmp_path / "none.json")]):
        try:
            parse_args(bad)
        except SystemExit:
            continue
        raise AssertionError(bad)
