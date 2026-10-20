"""The Accessibility Conformance Report (agent_access/acr.py): the VPAT 2.5 WCAG edition's layout and conformance terms,
mapped from the site report."""

import re
from html.parser import HTMLParser

from agent_access.acr import render_acr
from agent_access.models import AuditReport, CriterionResult, Finding, SiteReport, Status
from agent_access.team.orchestrator import AuditOutcome

PAGES = ("https://shop.example/", "https://shop.example/cart", "https://shop.example/help")


def result(n, title, level, status, page="", summary="", findings=()):
    return CriterionResult(rule_id=f"r{n}", wcag_criterion=n, title=title, level=level, status=status,
                           summary=summary or f"{status.value} on {page or 'the site'}", findings=list(findings))


def fail_on(n, page):
    return Finding(rule_id=f"r{n}", wcag_criterion=n, status=Status.FAIL, source="axe", check_id="x", page_url=page,
                   description=f"<b>{n}</b> fails here")


def site(target="AA"):
    # Per page: 1.1.1 fails on all three, 2.4.7 on one of three, 1.2.1 has no media on two pages and fails on the third.
    def page(url):
        i = PAGES.index(url)
        return AuditReport(page_url=url, target_level=target, counts={}, criteria=[
            result("1.1.1", "Non-text Content", "A", Status.FAIL, url),
            result("2.4.7", "Focus Visible", "AA", Status.FAIL if i == 0 else Status.PASS, url),
            result("1.2.1", "Audio-only and Video-only (Prerecorded)", "A", Status.FAIL if i == 2 else Status.NOT_APPLICABLE, url),
        ])
    criteria = [
        result("1.1.1", "Non-text Content", "A", Status.FAIL, findings=[fail_on("1.1.1", u) for u in PAGES]),
        result("1.2.1", "Audio-only and Video-only (Prerecorded)", "A", Status.FAIL, findings=[fail_on("1.2.1", PAGES[2])]),
        result("2.4.2", "Page Titled", "A", Status.PASS, summary="Passed: an auditor's judgement, confirmed by the reviewer."),
        result("1.2.2", "Captions (Prerecorded)", "A", Status.NOT_APPLICABLE),
        result("2.4.7", "Focus Visible", "AA", Status.FAIL, findings=[fail_on("2.4.7", PAGES[0])]),
        result("1.4.3", "Contrast (Minimum)", "AA", Status.NEEDS_REVIEW, summary="1 auditor judgement(s) not confirmed."),
    ]
    beyond = [result("1.4.6", "Contrast (Enhanced)", "AAA", Status.NEEDS_REVIEW)]
    if target == "AAA":
        criteria, beyond = criteria + beyond, []
    return SiteReport(target_level=target, pages=[page(u) for u in PAGES], counts={}, criteria=criteria,
                      beyond_target=beyond)


def rows(html: str) -> dict[str, tuple[str, str]]:
    return {m[1]: (m[2], m[3]) for m in re.finditer(
        r'<tr><th scope="row">(\S+) [^<]*</th><td>([^<]*)</td><td>(.*?)</td></tr>', html)}


def test_conformance_levels_follow_the_vpat_terms():
    html = render_acr(AuditOutcome(report=site(), engines={"axe-core": "axe-core 4.11"}, warnings=["page 2 not done"]))
    got = rows(html)
    assert got["1.1.1"][0] == "Does Not Support"   # fails on every page
    assert got["2.4.7"][0] == "Partially Supports"   # fails on 1 of 3
    assert got["1.2.1"][0] == "Does Not Support"   # fails on the only page it applies to
    assert got["2.4.2"][0] == "Supports" and got["1.2.2"][0] == "Not Applicable"
    assert got["1.4.3"][0] == "Not determined" and "A person must decide" in got["1.4.3"][1]
    assert got["1.1.1"][1].startswith("Fails on 3 of 3 audited page(s).")
    assert got["1.1.1"][1].count("<li>") == 3 and "&lt;b&gt;1.1.1&lt;/b&gt; fails here" in got["1.1.1"][1]   # escaped
    assert "1.4.6" not in got   # AAA isn't in scope at an AA target
    assert "Table 1: Success Criteria, Level A<" in html and "Table 2: Success Criteria, Level AA<" in html
    assert "Table 3" not in html and "Level AAA (No)" in html and "Level AA (Yes)" in html
    assert "1 criterion is Not determined" in html and "1 coverage warning(s)" in html
    assert "axe-core: axe-core 4.11" in html and "registered service marks of the Information Technology Industry" in html


def test_an_aaa_target_adds_table_3_where_undecided_is_not_evaluated():
    got = rows(html := render_acr(AuditOutcome(report=site("AAA"))))
    assert "Table 3: Success Criteria, Level AAA" in html and got["1.4.6"][0] == "Not Evaluated"


def test_the_report_is_well_formed_html():
    class Check(HTMLParser):
        void = {"meta", "br"}

        def __init__(self):
            super().__init__()
            self.open: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag not in self.void:
                self.open.append(tag)

        def handle_endtag(self, tag):
            assert self.open.pop() == tag

    parser = Check()
    parser.feed(render_acr(AuditOutcome(report=site())))
    assert parser.open == []
