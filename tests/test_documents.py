"""Linked PDFs (agent_access/documents.py): found on the audited pages, fetched, validated by veraPDF when installed,
and reported as their own items. veraPDF is replaced by a stand-in that prints its JSON, so the tests need no Java;
the last test runs the real CLI when `verapdf` is on PATH."""

import asyncio
import functools
import http.server
import json
import shutil
import sys
import threading

import pytest

from agent_access import documents
from agent_access.agent_tools.browser_tools import open_url
from agent_access.context import AuditContext
from agent_access.agent_tools.report_tools import compile_site_report
from agent_access.probes import collect
from agent_access.report_writer import render_markdown
from agent_access.team.orchestrator import AuditOutcome

PAGE = ('<html lang="en"><head><title>Fares</title></head><body><main><h1>Fares</h1>'
        '<a href="fares.pdf">Fares (PDF)</a> <a href="gone.pdf">Old fares (PDF)</a> <a href="fares.pdf#page=2">Page 2</a>'
        '<a href="https://elsewhere.example/x.pdf">Partner fares</a> <a href="about.html">About</a>'
        '<a href="old.pdf">2025 fares</a> <a href="partner.pdf">Partner timetable</a> <a href="big.pdf">Atlas</a>'
        '</main></body></html>')
# old.pdf moves to fares.pdf on this site; partner.pdf moves to another site, which the audit doesn't open.
REDIRECTS = {"/old.pdf": "/fares.pdf", "/partner.pdf": "https://elsewhere.example/timetable.pdf"}


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in REDIRECTS:
            self.send_response(302)
            self.send_header("Location", REDIRECTS[self.path])
            self.end_headers()
        else:
            super().do_GET()

    def log_message(self, *args):
        pass
# What veraPDF prints for one file with a figure that has no alternative text (PDF/UA-1 7.3-1).
STAND_IN = r'''
import json, sys
files = [a for a in sys.argv[1:] if a.endswith(".pdf")]
job = lambda f: {"itemDetails": {"name": f, "size": 1}, "validationResult": [{"profileName": "PDF/UA-1 validation profile",
       "compliant": False, "details": {"passedRules": 104, "failedRules": 1, "ruleSummaries": [{"clause": "7.3",
       "testNumber": 1, "status": "failed", "failedChecks": 1, "tags": ["figure", "alt-text", "structure"],
       "description": "Figure tags shall include an alternative representation"}]}}]}
print(json.dumps({"report": {"buildInformation": {"releaseDetails": [{"id": "apps", "version": "9.9"}]},
                              "jobs": [job(f) for f in files]}}))
'''


def serve(directory):
    handler = functools.partial(Handler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def audit_pdfs(tmp_path, pdf: bytes):
    (tmp_path / "index.html").write_text(PAGE)
    (tmp_path / "fares.pdf").write_bytes(pdf)
    (tmp_path / "big.pdf").write_bytes(b"%PDF" + b"0" * documents.MAX_BYTES)
    server, base = serve(tmp_path)

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            assert "error" not in await open_url(audit, f"{base}/index.html")
            await collect(audit, "page")
            found, warnings = await documents.check_documents(audit)
            outcome = AuditOutcome(report=compile_site_report(audit), documents=found)
            return base, found, warnings, dict(audit.engines), render_markdown(outcome)
    try:
        return asyncio.run(scenario())
    finally:
        server.shutdown()


def test_linked_pdfs_are_validated_and_mapped_to_criteria(tmp_path, monkeypatch):
    stand_in = tmp_path / "verapdf_stand_in.py"
    stand_in.write_text(STAND_IN)
    monkeypatch.setattr(documents, "verapdf_command", lambda: [sys.executable, str(stand_in)])
    base, found, warnings, engines, report = audit_pdfs(tmp_path, b"%PDF-1.7 a stand-in document")
    # Same site, once each, in link order.
    assert [d["url"] for d in found] == [f"{base}/{n}.pdf" for n in ("fares", "gone", "old", "partner", "big")]
    fares, gone, old, partner, big = found
    assert fares["checked"] and not fares["compliant"] and fares["failed_rules"][0]["criteria"] == ["1.1.1", "1.3.1"]
    assert old["checked"]   # a redirect on the same site is followed
    assert not gone["checked"] and gone["reason"] == "HTTP 404"
    assert not partner["checked"] and partner["reason"].startswith("it redirects to https://elsewhere.example/timetable.pdf")
    assert not big["checked"] and big["reason"] == "over 20 MB"   # by its Content-Length, unread
    assert warnings[0].startswith("3 linked PDF(s) were not checked: ")
    assert engines["veraPDF"] == "veraPDF 9.9 (PDF/UA-1, linked PDFs)"
    assert "## Linked documents" in report and "  - 7.3-1 (WCAG 1.1.1, 1.3.1): Figure tags" in report
    assert f"- {base}/gone.pdf: not checked (HTTP 404)" in report


def test_without_verapdf_the_linked_pdfs_are_a_warning_not_a_silence(tmp_path, monkeypatch):
    monkeypatch.setattr(documents, "verapdf_command", lambda: None)
    _, found, warnings, _, _ = audit_pdfs(tmp_path, b"%PDF-1.7")
    assert [d["checked"] for d in found] == [False] * 5
    assert warnings == ["5 linked PDF(s) were not checked: veraPDF isn't installed (it needs Java; put verapdf on PATH)."]


@pytest.mark.skipif(shutil.which("verapdf") is None, reason="veraPDF isn't installed")
def test_the_real_verapdf_fails_an_untagged_pdf(tmp_path):
    from playwright.async_api import async_playwright

    async def untagged() -> bytes:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_content("<h1>Fares</h1><p>Off-peak fares apply after 09:30.</p>")
            pdf = await page.pdf(tagged=False)
            await browser.close()
            return pdf
    _, found, _, engines, _ = audit_pdfs(tmp_path, asyncio.run(untagged()))
    fares = found[0]
    assert fares["checked"] and not fares["compliant"] and engines["veraPDF"].startswith("veraPDF 1.")
    assert "1.3.1" in {c for r in fares["failed_rules"] for c in r["criteria"]}   # no structure tree
    json.dumps(found)   # it goes to report.json as is
