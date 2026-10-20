"""The single WCAG auditor (agent_access/auditor.py): the LLM calls one tool per criterion, records the results, and
moves to the next page. Scripted with RouterModel, so no model calls."""

import asyncio
import functools
import http.server
import threading

import pytest
from test_agent import RouterModel, call, first_user_text

from agent_access.agent_tools.browser_tools import load_html_content
from agent_access.cli import parse_args
from agent_access.context import AuditContext
from agent_access.models import Status
from agent_access.team.orchestrator import run_audit

PAGE = ('<html lang="en"><head><title>Contact us - Shop</title></head><body><main><h1>Contact us</h1>'
        '<img src="map.png"><p>Write to us at the shop.</p></main></body></html>')


def test_the_agent_calls_the_criterion_tools_and_its_results_stand():
    prompts = []

    def finish(input):
        prompts.append(first_user_text(input))
        return "Recorded."
    model = RouterModel({"WCAG auditor": [
        call("check_non_text_content", {}),
        call("check_page_title", {}),
        call("record_findings", {"judgements": [{"criterion": "2.4.2", "status": "pass",
                                                 "description": "The title names the page and the site.",
                                                 "evidence": "Title 'Contact us - Shop'.", "selectors": ["title"]}]}),
        finish,   # the first run ends with most criteria still without a result
        finish,   # the one more run it gets for them
    ]})

    async def scenario():
        # Eager measurement would run every check before the agent; the single auditor's checks run when it calls them.
        async with AuditContext(screen_reader="emulated", measurement="eager") as audit:
            await load_html_content(audit, PAGE, "file:page.html")
            return audit, await run_audit(audit, model=model, mode="single", progress=lambda m: None)
    audit, outcome = asyncio.run(scenario())
    assert not model.scripts["WCAG auditor"] and outcome.errors == []
    tools = model.tools_seen["WCAG auditor"]
    assert len([t for t in tools if t.startswith("check_")]) == 55 and "record_findings" in tools   # one per AA criterion
    runs = [r["criteria"] for r in outcome.tool_runs]
    assert runs[:2] == ["1.1.1", "2.4.2"]                  # the agent called these itself, nothing ran before
    assert len(set(runs)) == 55                            # the rest ran anyway once the agent stopped
    assert prompts[0] == "Audit file:page.html." and "still have no result" in prompts[1]
    results = {c.wcag_criterion: c for c in outcome.report.criteria}
    assert results["2.4.2"].status is Status.PASS            # no reviewer: the auditor's pass stands
    assert results["1.1.1"].status is Status.FAIL            # the check found the image without alt text
    assert any("no second agent re-checked it" in n for n in outcome.report.notes)
    assert any("only the ones an agent chose to check were tested" in n for n in outcome.report.notes)   # not "also tested"
    assert outcome.warnings and "recorded no result for" in outcome.warnings[0]
    assert outcome.engines["Agent models"].startswith("auditor:")


def test_it_moves_on_to_the_next_page(tmp_path):
    (tmp_path / "index.html").write_text(PAGE.replace("</main>", '<a href="b.html">Opening hours</a></main>'))
    (tmp_path / "b.html").write_text(PAGE.replace("Contact us", "Opening hours"))
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *args: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    prompts = []
    model = RouterModel({"WCAG auditor": [lambda input: prompts.append(first_user_text(input)) or "Done."] * 4})

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            return await run_audit(audit, start_url=f"{base}/index.html", pages=2, model=model, mode="single",
                                   progress=lambda m: None)
    try:
        outcome = asyncio.run(scenario())
    finally:
        server.shutdown()
    assert [p.page_url for p in outcome.report.pages] == [f"{base}/index.html", f"{base}/b.html"]
    assert prompts[0] == f"Audit {base}/index.html." and prompts[2] == f"Audit {base}/b.html."


def test_the_cli_runs_the_single_auditor_by_default():
    assert parse_args(["https://shop.example/"]).mode == "single"
    for team_only in (["--sr-agent"], ["--measurement", "eager"]):   # team features, not the single auditor's
        with pytest.raises(SystemExit):
            parse_args(["https://shop.example/", *team_only])


def test_its_not_applicable_is_recorded_with_what_the_page_controls_reveal():
    """It has no tools to open a menu or dialog; the disclosures probe opened them. A team agent is refused a
    not-applicable until it opens them and labels the state; the single auditor's is recorded with that content."""
    from agent_access.agent_tools.report_tools import add_manual_finding
    from agent_access.probes import ProbeResult

    for reviewed in (True, False):
        audit = AuditContext()
        audit.verdicts_reviewed = reviewed
        audit.set_current("page")
        audit.observations["page"] = {"disclosures": ProbeResult({"results": [
            {"trigger": "#more", "name": "More", "opened_by": "Enter", "content": "Our video tour",
             "aria_expanded": {"before": "false", "after": "true"}, "is_dialog": False}]})}
        result = add_manual_finding(audit, "1.2.2", "not_applicable", "No media.", "No video or audio element.")
        if reviewed:
            assert "can't be not applicable yet" in result["error"] and not audit.findings
        else:
            assert result["recorded"] == "1.2.2" and "'More' (#more) shows 'Our video tour'" in result["note"]
