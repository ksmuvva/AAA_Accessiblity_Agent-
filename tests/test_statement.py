"""The UK accessibility statement check (agent_access/statement.py): the statement's link is found on the site's
homepage, the statement is read in a tab of its own, and each part GOV.UK's model statement requires is looked for by
its wording, then the claimed compliance is compared with the audit's failures."""

import asyncio
import functools
import http.server
import threading
from datetime import date

from agent_access import statement
from agent_access.agent_tools.browser_tools import open_url
from agent_access.agent_tools.report_tools import compile_site_report
from agent_access.context import AuditContext
from agent_access.models import CriterionResult, Finding, SiteReport, Status
from agent_access.report_writer import render_markdown
from agent_access.team.orchestrator import AuditOutcome

# The statement's link comes after 250 others, past the 200 links the page probe keeps.
HOME = ('<html lang="en"><head><title>Fares</title></head><body><main><h1>Fares</h1><img src="map.png">'
        + "".join(f'<a href="/p{i}.html">Page {i}</a> ' for i in range(250))
        + '</main><footer><a href="/help/accessibility-statement.html">Accessibility statement</a></footer></body></html>')
# Claims full compliance with WCAG 2.1, prepared long ago, names the EHRC but doesn't link to EASS.
STALE = ('<html lang="en"><head><title>Accessibility statement</title></head><body><main><h1>Accessibility statement '
         'for Fares</h1><p>This website is fully compliant with the Web Content Accessibility Guidelines version 2.1 AA '
         'standard.</p><p>If you need information in a different format email fares@example.gov.uk.</p><p>The Equality '
         'and Human Rights Commission (EHRC) is responsible for enforcing the accessibility regulations.</p><p>This '
         'statement was prepared on 1 January 2020.</p></main></body></html>')


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/help/moved.html":   # the statement moved to another site, which the audit doesn't open
            self.send_response(302)
            self.send_header("Location", "https://elsewhere.example/statement")
            self.end_headers()
        else:
            super().do_GET()

    def log_message(self, *args):
        pass


def serve(directory):
    handler = functools.partial(Handler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def audit_statement(tmp_path, home: str):
    (tmp_path / "index.html").write_text(home)
    (tmp_path / "help").mkdir()
    (tmp_path / "help" / "accessibility-statement.html").write_text(STALE)
    server, base = serve(tmp_path)

    async def scenario():
        async with AuditContext(screen_reader="emulated", uk_statement=True) as audit:
            assert "error" not in await open_url(audit, f"{base}/index.html")
            # The audit's one failure (recorded directly: running the checks on 250 links takes long).
            audit.findings.append(Finding(rule_id="WCAG_1_1_1", wcag_criterion="1.1.1", status=Status.FAIL,
                                          source="axe", check_id="image-alt", page_url=f"{base}/index.html",
                                          description="The image has no alternative text."))
            report = compile_site_report(audit)
            found, warnings = await statement.check_statement(audit, report)
            outcome = AuditOutcome(report=report, statement=found)
            return base, found, warnings, list(audit.pages), render_markdown(outcome)
    try:
        return asyncio.run(scenario())
    finally:
        server.shutdown()


def test_a_stale_full_compliance_claim_is_checked_against_the_audit(tmp_path):
    base, found, warnings, pages, report = audit_statement(tmp_path, HOME)
    assert warnings == [] and found["checked"]
    assert found["url"] == f"{base}/help/accessibility-statement.html" and found["linked_from"] == f"{base}/"
    assert pages == [f"{base}/index.html"]   # the statement is read, not audited
    rows = {r["requirement"]: r for r in found["checks"]}
    assert found["claim"] == "fully"
    assert rows["Names WCAG 2.2"]["found"] is False and "2.1" in rows["Names WCAG 2.2"]["detail"]
    assert rows["Says when it was prepared"]["detail"] == "2020-01-01"
    assert rows["Reviewed in the last 12 months"]["found"] is False
    assert rows["How to give feedback or ask for an accessible format"]["detail"] == "email"
    enforcement = rows["Enforcement procedure: the EHRC, with a link to EASS (the ECNI in Northern Ireland)"]
    assert enforcement["found"] is False and enforcement["detail"] == "no link to EASS or the ECNI"
    assert rows["Names the 2018 accessibility regulations"]["found"] is False
    claim = rows["Agrees with this audit's Level A and AA failures"]   # the image has no alternative text
    assert claim["found"] is False and "1.1.1" in claim["detail"]
    assert "## UK accessibility statement" in report and "| Names WCAG 2.2 | Not found |" in report


def test_no_statement_link_is_a_warning(tmp_path):
    _, found, warnings, _, report = audit_statement(tmp_path, HOME.replace("Accessibility statement", "Contact")
                                                    .replace("/help/accessibility-statement.html", "/contact.html"))
    assert not found["checked"] and found["reason"].startswith("no link to an accessibility statement was found on ")
    assert warnings == [f"The UK accessibility statement was not checked: {found['reason']}."]
    assert "Not checked: no link to an accessibility statement" in report


def test_a_redirect_off_the_site_is_not_followed(tmp_path):
    base, found, warnings, _, _ = audit_statement(tmp_path, HOME.replace("accessibility-statement.html", "moved.html"))
    assert found["reason"] == (f"{base}/help/moved.html could not be read (it redirects to https://elsewhere.example/"
                               "statement, which the audit doesn't open: it is not on the audited site)")
    assert warnings == [f"The UK accessibility statement was not checked: {found['reason']}."]


def failing(*criteria: tuple[str, str]) -> SiteReport:
    results = [CriterionResult(rule_id=f"r{n}", wcag_criterion=n, title=t, level="A", status=Status.FAIL, summary="")
               for n, t in criteria]
    return SiteReport(target_level="AA", pages=[], counts={}, criteria=results)


def test_a_partial_compliance_statement_must_list_what_the_audit_failed():
    today = date.today()
    text = (f"This website is partially compliant with the Web Content Accessibility Guidelines (WCAG) 2.2 AA standard, "
            f"due to the non-compliances listed below. Non-accessible content. Some images have no text alternative "
            f"(success criterion 1.1.1). The Public Sector Bodies (Websites and Mobile Applications) (No. 2) "
            f"Accessibility Regulations 2018. Call 020 7946 0000. This statement was prepared on 3 March 2019. It was "
            f"last reviewed on {today.day} {today.strftime('%B')} {today.year}. Accessibility tests against WCAG 2.2 "
            f"AA were carried out on 1 May 2026.")
    links = [{"text": "contact the Equality Advisory and Support Service (EASS)",
              "href": "https://www.equalityadvisoryservice.com/"}]
    claim, rows = statement.assess(text, links, failing(("1.1.1", "Non-text Content"), ("2.4.7", "Focus Visible"),
                                                        ("2.1.1", "Keyboard")), today)
    rows = {r["requirement"]: r for r in rows}
    assert claim == "partially"
    assert all(r["found"] for name, r in rows.items() if name != "Agrees with this audit's Level A and AA failures"
               and not name.startswith("Enforcement"))
    assert rows["Enforcement procedure: the EHRC, with a link to EASS (the ECNI in Northern Ireland)"]["detail"] == (
        "the enforcement body isn't named")
    listed = rows["Agrees with this audit's Level A and AA failures"]
    assert listed["found"] is False and listed["detail"] == "doesn't mention 2.4.7 Focus Visible, 2.1.1 Keyboard"


def test_a_headline_about_accessibility_is_not_the_statement_link():
    links = [{"text": "Accessibility of new homes: consultation analysis", "href": "https://x.gov.scot/publications/new-homes-accessibility/"},
             {"text": "Read the accessibility report", "href": "https://x.gov.scot/news/accessibility-report-2026"},
             {"text": "Accessibility help", "href": "https://x.gov.scot/accessibility/"}]
    assert statement.statement_link(links, "https://x.gov.scot/") == "https://x.gov.scot/accessibility/"
    assert statement.statement_link(links[:2], "https://x.gov.scot/") is None


def test_uk_public_sector_hosts():
    assert all(map(statement.uk_public_sector, ("www.gov.uk", "gov.uk", "www.nhs.uk", "www.ox.ac.uk", "gov.wales")))
    assert not any(map(statement.uk_public_sector, ("example.com", "notgov.uk", "www.bbc.co.uk")))
