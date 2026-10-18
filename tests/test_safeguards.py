"""Safeguards in the agent design: what the reviewer may change, how judgements combine, interaction safety,
context trimming, per-page orchestration, and fault isolation. Scripted fake models; no API calls."""

import asyncio
import functools
import http.server
import json
import threading

from agents.exceptions import ModelBehaviorError
from agents.run_config import CallModelData, ModelInputData
from test_agent import SUMMARY, RouterModel, call, confirm_all, first_user_text

from agent_access.team.orchestrator import run_audit
from agent_access.team.reviewer import apply_verdicts, review_findings
from agent_access.team.runtime import IMAGE_PLACEHOLDER, KEEP_IMAGES, OLD_OUTPUT_CHARS, trim_input
from agent_access.agent_tools.browser_tools import click, load_html_content
from agent_access.context import AuditContext
from agent_access.models import Finding, ReviewResult, ReviewVerdict, Status
from agent_access.agent_tools.report_tools import _criterion_status, add_manual_finding, compile_report


def finding(status: Status, source: str, criterion: str = "1.1.1", page: str = "page") -> Finding:
    return Finding(rule_id=f"WCAG_{criterion.replace('.', '_')}", wcag_criterion=criterion, status=status, source=source,
                   check_id="manual" if source == "agent" else "check_x", page_url=page, description="x")


def verdicts(findings, verdict):
    return ReviewResult(verdicts=[ReviewVerdict(finding_id=f.id, verdict=verdict, reason="r") for f in findings])


def test_reviewer_verdict_rules():
    measured, judged = finding(Status.FAIL, "axe"), finding(Status.FAIL, "agent")
    apply_verdicts([measured, judged], verdicts([measured, judged], "uncertain"))
    # Doubt keeps a measured failure (it may only show during interaction) but not an agent's judgement.
    assert measured.status is Status.FAIL and measured.original_status is None
    assert judged.status is Status.NEEDS_REVIEW and judged.original_status is Status.FAIL

    rejected = finding(Status.FAIL, "custom")
    log = apply_verdicts([rejected], verdicts([rejected], "rejected"))
    assert rejected.status is Status.NEEDS_REVIEW and log[0]["demoted"] and log[0]["status"] == "fail"

    # Agent passes are verified too; doubt sends them to needs_review, never to fail.
    passes = [finding(Status.PASS, "agent") for _ in range(3)]
    for f, v in zip(passes, ("confirmed", "uncertain", "rejected")):
        apply_verdicts([f], verdicts([f], v))
    assert [f.status for f in passes] == [Status.PASS, Status.NEEDS_REVIEW, Status.NEEDS_REVIEW]


def test_demoted_failure_never_becomes_a_pass():
    fail, agent_pass = finding(Status.FAIL, "axe"), finding(Status.PASS, "agent")
    apply_verdicts([fail], verdicts([fail], "rejected"))
    assert _criterion_status([fail, agent_pass])[0] is Status.NEEDS_REVIEW
    assert _criterion_status([agent_pass])[0] is Status.PASS


def test_agent_judgements_replace_each_other_but_not_failures():
    audit = AuditContext()
    audit.current_url = "page"
    add_manual_finding(audit, "2.4.2", "needs_review", "Unsure.", "Title is 'Home'.")
    assert add_manual_finding(audit, "2.4.2", "pass", "Descriptive.", "Title is 'Home - Shop'.")["replaced_earlier_judgements"] == 1
    assert [f.status for f in audit.findings] == [Status.PASS]

    add_manual_finding(audit, "1.1.1", "fail", "Logo alt is 'img'.", "alt='img'", ["#logo"])
    add_manual_finding(audit, "1.1.1", "fail", "Hero alt is a file name.", "alt='hero.jpg'", ["#hero"])
    refused = add_manual_finding(audit, "1.1.1", "pass", "Fine.", "Looks fine.")
    assert "error" in refused and "reviewer" in refused["error"]
    assert {c.wcag_criterion: c.status for c in compile_report(audit).criteria}["2.4.2"] is Status.NEEDS_REVIEW  # unreviewed
    for f in audit.findings:
        f.review_verdict = "confirmed"
    by = {c.wcag_criterion: c for c in compile_report(audit).criteria}
    assert by["1.1.1"].status is Status.FAIL and len(by["1.1.1"].findings) == 2
    assert by["2.4.2"].status is Status.PASS


def test_trim_input_keeps_newest_images_and_shortens_old_outputs():
    image = {"type": "input_image", "image_url": "data:image/jpeg;base64,AAAA"}
    items = []
    for n in range(4):
        items.append({"type": "function_call_output", "call_id": f"img{n}", "output": [{"type": "input_text", "text": "s"}, image]})
    for n in range(15):
        items.append({"type": "function_call_output", "call_id": f"txt{n}", "output": "y" * (OLD_OUTPUT_CHARS * 2)})
    original = json.dumps(items)
    trimmed = trim_input(CallModelData(model_data=ModelInputData(input=items, instructions="i"), agent=None,
                                       context=None)).input
    assert json.dumps(items) == original  # the run's own history is untouched
    images = [p for item in trimmed[:4] for p in item["output"] if p.get("type") == "input_image"]
    assert len(images) == KEEP_IMAGES and trimmed[0]["output"][1] == IMAGE_PLACEHOLDER
    assert trimmed[3]["output"][1] == image
    assert len(trimmed[4]["output"]) < OLD_OUTPUT_CHARS * 2 and len(trimmed[-1]["output"]) == OLD_OUTPUT_CHARS * 2
    assert [i["call_id"] for i in trimmed] == [i["call_id"] for i in items]


SAVE_PAGE = """<html lang="en"><head><title>Settings</title></head><body><main>
<button id="save" onclick="fetch('http://127.0.0.1:9/api/save', {method: 'POST', body: 'x'}).catch(() => {})">Save</button>
</main></body></html>"""


def test_free_form_interaction_is_network_guarded():
    async def scenario():
        async with AuditContext(allow_interaction=True, screen_reader="emulated") as audit:
            loaded = await load_html_content(audit, SAVE_PAGE)
            blocked = await click(audit, "#save")
            changed = loaded["page_label"] in audit.dirty
            audit.allowed_writes = ("http://127.0.0.1:9/api/",)
            allowed = await click(audit, "#save")
            await audit.restore_page()
            return blocked, changed, allowed, loaded["page_label"] in audit.dirty

    blocked, changed, allowed, still_dirty = asyncio.run(scenario())
    assert any(r.startswith("POST http://127.0.0.1:9/api/save") for r in blocked["blocked_requests"])
    assert "--allow-submit" in blocked["note"]
    assert changed and not still_dirty  # interaction marks the page changed; restoring it clears that
    assert "blocked_requests" not in allowed


def test_a_failing_review_batch_does_not_stop_the_rest():
    batch_error = ModelBehaviorError("bad output")

    def explode(_input):
        raise batch_error

    model = RouterModel({"reviewer": [explode, confirm_all]})

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            loaded = await load_html_content(audit, "<html lang='en'><title>t</title><body><img src='a.png'></body></html>")
            audit.add_findings([finding(Status.FAIL, "agent", page=loaded["page_label"]) for _ in range(30)])
            log, errors = await review_findings(audit, model)
            return audit, log, errors

    audit, log, errors = asyncio.run(scenario())
    assert len(errors) == 1 and "ModelBehaviorError" in errors[0]
    assert [r["verdict"] for r in log].count("not_reviewed") == 25 and [r["verdict"] for r in log].count("confirmed") == 5
    assert all(f.status is Status.FAIL for f in audit.findings)  # unreviewed failures keep their recorded status...
    assert compile_report(audit).counts["fail"] == 1             # ...and the confirmed ones carry the report


SITE = {
    "index.html": '<a href="/a.html">About</a> <a href="/b.html">Contact</a> <a href="/c.html">Blog</a>',
    "a.html": "About us.", "b.html": "Contact us.", "c.html": "Our blog.",
}


def test_multi_page_audit_runs_the_lead_once_per_page_in_code(tmp_path):
    for name, body in SITE.items():
        (tmp_path / name).write_text(f"<html lang='en'><head><title>{name}</title></head><body><main><h1>{name}</h1>"
                                     f"<p>{body}</p></main></body></html>", encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    model = RouterModel({
        # The picker's off-site pick is ignored: only real candidate links are audited.
        "page picker": [{"urls": ["https://evil.example/", f"{base}/b.html"]}],
        "lead auditor": [{"notes_for_human": ["note"]}] * 4,
        "reviewer": [confirm_all] * 6,
        "summary writer": [SUMMARY],
    })

    async def scenario():
        async with AuditContext(output_dir=tmp_path, screen_reader="emulated") as audit:
            return await run_audit(audit, start_url=f"{base}/index.html", pages=2, model=model, max_turns=5,
                                   progress=lambda m: None)

    try:
        outcome = asyncio.run(scenario())
    finally:
        server.shutdown()
    assert [p.page_url for p in outcome.report.pages] == [f"{base}/index.html", f"{base}/b.html"]
    prompts = [first_user_text(i) for i in model.inputs["lead auditor"]]
    # Each page: a fresh first run, then a fresh continuation run because criteria stayed open.
    assert [p.split(":", 1)[0] for p in prompts] == ["Audit this page", "Continue auditing http"] * 2
    assert f"{base}/b.html" in prompts[2]
    assert outcome.lead_notes[0] == f"{base}/index.html: note"
    assert sum("stopped with" in w for w in outcome.warnings) == 2
    assert "reviewer" in outcome.engines["Agent models"]


def test_a_state_the_lead_labels_gets_its_own_lead_runs_in_code_mode():
    """The eval's settings_2: the lead labelled "after-save-click" and stopped, leaving 51 criteria open there, which
    turned three correct passes into needs_review. A rebuildable state is now judged like a page."""
    page = ("<html lang='en'><head><title>Menu</title></head><body><main><h1>Menu</h1><button id='open' "
            "onclick=\"document.getElementById('m').hidden=false\">Open</button><p id='m' hidden>Settings</p></main></body></html>")
    model = RouterModel({
        "lead auditor": [call("click_element", {"selector": "#open"}), call("label_page_state", {"label": "menu-open"}),
                         {"notes_for_human": []}] + [{"notes_for_human": []}] * 6,
        "reviewer": [confirm_all] * 6, "summary writer": [SUMMARY],
    })

    async def scenario():
        async with AuditContext(allow_interaction=True, screen_reader="emulated") as audit:
            await load_html_content(audit, page)
            await run_audit(audit, model=model, max_turns=5, progress=lambda m: None)
            return audit

    audit = asyncio.run(scenario())
    state = next(k for k in audit.pages if k.endswith("[menu-open]"))
    prompts = [first_user_text(i) for i in model.inputs["lead auditor"]]
    assert f"Audit this page: {state}" in prompts


def test_restore_page_works_inside_the_network_guard_on_http_pages(tmp_path):
    """Interactive probes restore the page from inside network_guard. The guard answers navigations with 204, which
    would abort the reload on a real URL, so restore_page's own navigation must be let through."""
    from agent_access.agent_tools.browser_tools import open_url

    (tmp_path / "index.html").write_text("<html lang='en'><head><title>Home</title></head><body><main><h1>Home</h1>"
                                         "<a href='/other.html'>Other</a></main></body></html>", encoding="utf-8")
    (tmp_path / "other.html").write_text("<html lang='en'><head><title>Other</title></head><body>o</body></html>", encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            await open_url(audit, f"{base}/index.html")
            page = await audit.page()
            async with audit.network_guard() as blocked:
                await page.click("a")  # a navigation the guard must still block
                await page.wait_for_timeout(300)
                still_home = await page.title()
                restored = await audit.restore_page()
                title_after = await page.title()
            return still_home, blocked, restored, title_after

    try:
        still_home, blocked, restored, title_after = asyncio.run(scenario())
    finally:
        server.shutdown()
    assert still_home == "Home" and any("other.html (navigation)" in b for b in blocked)
    assert restored and title_after == "Home"


def test_a_check_whose_probe_failed_says_so_to_the_agent():
    """A probe error must not look like a clean 'needs review': the finding and get_criterion both name it."""
    from agent_access.probes import PROBES, Probe
    from agent_access.engine import criterion_view, run_rule
    from agent_access.rules import REGISTRY

    async def boom(audit, **kwargs):
        raise RuntimeError("hover probe exploded")

    original = PROBES["hover"]
    PROBES["hover"] = Probe("hover", boom, True)

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            await load_html_content(audit, "<html lang='en'><title>t</title><body><a href='/' title='tip'>x</a></body></html>")
            result = await run_rule(audit, REGISTRY["1.4.13"])
            return result, criterion_view(audit, "1.4.13"), audit

    try:
        result, view, audit = asyncio.run(scenario())
    finally:
        PROBES["hover"] = original
    assert result["probe_errors"] == ["hover: RuntimeError: hover probe exploded"]
    assert result["findings"][0]["status"] == "needs_review" and "could not run" in result["findings"][0]["description"]
    assert "hover probe exploded" in view["automated_check_error"] and view["status"] == "needs_review"
    assert not audit.tool_runs[-1]["ok"]


def test_a_rule_that_raises_still_records_a_finding():
    """A crashing rule must not leave its criterion 'not tested' after a sweep: it records a 'could not run' review."""
    import dataclasses
    from agent_access.engine import criterion_view, run_rule
    from agent_access.rules import REGISTRY

    def broken(ctx):
        raise KeyError("stops")

    tool = dataclasses.replace(REGISTRY["2.2.1"], impl=broken)

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            await load_html_content(audit, "<html lang='en'><title>t</title><body><p>hi</p></body></html>")
            result = await run_rule(audit, tool)
            return result, criterion_view(audit, "2.2.1")

    result, view = asyncio.run(scenario())
    assert result["error"] == "KeyError: 'stops'" and result["result"] == {"needs_review": 1}
    assert "could not run" in result["findings"][0]["description"] and "KeyError" in result["findings"][0]["description"]
    assert view["status"] == "needs_review" and "KeyError" in view["automated_check_error"]
