"""Tool tests against local HTML fixtures. No network or OpenAI API calls."""

import asyncio
from pathlib import Path

from agent_access.catalogue.catalogue import load_catalogue
from agent_access.agent_tools.browser_tools import load_html_content, page_outline
from agent_access.context import AuditContext
from agent_access.engine import run_rule
from agent_access.models import Status
from agent_access.probes import collect
from agent_access.probes.axe import criteria_from_tags
from agent_access.rules import REGISTRY
from agent_access.agent_tools.report_tools import add_manual_finding, compile_report

FIXTURES = Path(__file__).parent / "fixtures"


def run(coro):
    return asyncio.run(coro)


def test_catalogue_counts_and_lookup():
    catalogue = load_catalogue()
    assert len(catalogue.rules) == 86
    assert len(catalogue.up_to_level("AA")) == 55
    assert "4.1.1" in catalogue.removed_criteria
    assert catalogue.get("4.1.1") is None and catalogue.get("9.9.9") is None
    assert catalogue.get("1.4.3").title == "Contrast (Minimum)"
    assert catalogue.get("WCAG_1_4_3").wcag_criterion == "1.4.3"


def test_criteria_from_tags():
    tags = ["cat.text-alternatives", "wcag2a", "wcag111", "wcag1412", "wcag258", "best-practice"]
    assert criteria_from_tags(tags) == ["1.1.1", "1.4.12", "2.5.8"]


def test_report_without_evidence_is_all_not_tested():
    audit = AuditContext()
    audit.current_url = "about:inline-html"
    report = compile_report(audit)
    assert report.counts["not_tested"] == 55
    assert report.counts["pass"] == 0


def test_axe_scan_and_report_on_bad_page():
    async def scenario():
        async with AuditContext() as audit:
            await load_html_content(audit, (FIXTURES / "bad_page.html").read_text(encoding="utf-8"))
            outline = await page_outline(audit)
            scan = (await collect(audit, "axe")).data
            for criterion in ("1.1.1", "3.1.1", "2.4.2", "1.4.3", "4.1.2"):
                await run_rule(audit, REGISTRY[criterion])
            return audit, outline, scan, compile_report(audit)

    audit, outline, scan, report = run(scenario())

    assert outline["lang"] is None
    assert outline["counts"]["images_without_alt_attribute"] == 1

    violations = {r["id"] for r in scan["rules"] if r["outcome"] == "fail"}
    assert {"image-alt", "html-has-lang", "document-title", "label", "button-name", "color-contrast"} <= violations
    assert not any("4.1.1" in r["criteria"] for r in scan["rules"])

    by_criterion = {c.wcag_criterion: c for c in report.criteria}
    for criterion in ("1.1.1", "3.1.1", "2.4.2", "1.4.3", "4.1.2"):
        assert by_criterion[criterion].status is Status.FAIL, criterion
    # Nothing on this page can be a pass: automated passes alone only reach needs_review.
    assert report.counts["pass"] == 0
    assert report.counts["not_tested"] > 0


def test_outline_reports_language_parts():
    html = '<html lang="en"><body><p>Hello</p><p lang="fr">Bonjour</p><p lang="EN">Same as page</p></body></html>'

    async def scenario():
        async with AuditContext() as audit:
            await load_html_content(audit, html)
            return await page_outline(audit)

    outline = run(scenario())
    assert outline["language_parts"] == [{"tag": "p", "lang": "fr", "text": "Bonjour"}]


def test_rerunning_a_rule_does_not_duplicate_findings():
    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            await load_html_content(audit, (FIXTURES / "bad_page.html").read_text(encoding="utf-8"))
            await run_rule(audit, REGISTRY["1.1.1"])
            first = len(audit.findings)
            await run_rule(audit, REGISTRY["1.1.1"])
            return first, len(audit.findings)

    first, second = run(scenario())
    assert first == second > 0


def test_manual_finding_precedence():
    audit = AuditContext()
    audit.current_url = "about:inline-html"
    assert "error" in add_manual_finding(audit, "4.1.1", "pass", "x", "y")
    assert "error" in add_manual_finding(audit, "2.4.2", "pass", "x", "  ")

    add_manual_finding(audit, "2.4.2", "pass", "Title is descriptive.", "Title is 'Checkout - Shop'.")
    add_manual_finding(audit, "3.1.1", "pass", "Language set.", "lang='en'.")
    add_manual_finding(audit, "3.1.1", "fail", "Language wrong.", "Page is French but lang='en'.")

    by_criterion = {c.wcag_criterion: c for c in compile_report(audit).criteria}
    assert by_criterion["2.4.2"].status is Status.PASS
    assert by_criterion["3.1.1"].status is Status.FAIL  # a failure always wins over a pass
