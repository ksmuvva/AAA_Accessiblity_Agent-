"""Regressions found by benchmarking against the W3C Before and After Demonstration (BAD).

bad_demo_page.html reproduces BAD patterns the rules used to pass; the inline "good" pages reproduce patterns from
BAD's fixed pages that the rules used to fail.
"""

import asyncio

from audit_helpers import judge, statuses
from agent_access.agent_tools.browser_tools import load_html_content
from agent_access.context import AuditContext
from agent_access.engine import run_rule
from agent_access.models import Status
from agent_access.rules import REGISTRY

FILLER = " ".join(["Residents and visitors can read the latest city news and plan their weekend here."] * 30)


def test_bad_demo_patterns_are_no_longer_passed():
    audit = judge("bad_demo_page.html", "2.4.1", "2.4.9", "2.4.10", "1.3.1")
    # The skip link works, but lands before the demo's own navigation, not on the main content.
    assert Status.NEEDS_REVIEW in statuses(audit, "2.4.1")
    assert not any(f.status is Status.PASS and "Skip link" in f.description for f in audit.findings)
    # Image links without alt text have no link text at all (F89).
    nameless = [f for f in audit.findings if f.wcag_criterion == "2.4.9" and f.status is Status.FAIL]
    assert nameless and set(nameless[0].selectors) == {"#nav-home", "#nav-news"}
    # Styled-text "headings" mean the sections aren't organised by headings.
    assert statuses(audit, "2.4.10") == {Status.NEEDS_REVIEW}
    # An unlabelled field has no programmatic label (F68).
    assert any(f.status is Status.NEEDS_REVIEW and "#quickmenu" in f.selectors
               for f in audit.findings if f.wcag_criterion == "1.3.1")


def test_skip_link_to_main_content_still_passes():
    html = ('<html lang="en"><body><a href="#main">Skip to main content</a><nav><a href="/a">A</a></nav>'
            '<main id="main"><h1>Title</h1><p>Text.</p></main></body></html>')
    assert statuses(judge(html, "2.4.1"), "2.4.1") >= {Status.PASS}
    assert Status.NEEDS_REVIEW not in statuses(judge(html, "2.4.1"), "2.4.1")


def test_fixed_page_patterns_are_not_failed():
    html = (f'<html lang="en"><body><main><h1>News</h1><h2>Your shout</h2><p style="width: 1100px">{FILLER}</p>'
            '<p><a href="/offsite">Clara F., 15 months</a>, via email</p>'
            '<fieldset><legend>Greenest City</legend><p>Which city do you find is the greenest?</p>'
            '<select id="cc" title="cities of the world"><option>Select a city</option></select></fieldset>'
            '</main></body></html>')
    audit = judge(html, "1.4.8", "2.5.3", "3.3.2")
    # Long lines at one width aren't a failure: narrowing the window is a mechanism (H87).
    assert Status.FAIL not in statuses(audit, "1.4.8") and Status.NEEDS_REVIEW in statuses(audit, "1.4.8")
    # A name with a comma ("Clara F., 15 months, link") contains its visible label.
    assert statuses(audit, "2.5.3") == {Status.PASS}
    # A select named by title, with a visible question, isn't "unlabelled".
    assert Status.FAIL not in statuses(audit, "3.3.2")


def test_repeated_image_links_without_names_are_not_consistently_identified():
    nav = '<a href="/home.html"><img src="h.gif" width="80" height="20"></a><a href="/news.html">News</a>'
    pages = {"a": f'<html lang="en"><body>{nav}<p>Page A</p></body></html>',
             "b": f'<html lang="en"><body>{nav}<p>Page B</p></body></html>'}

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            for label, html in pages.items():
                await load_html_content(audit, html, label)
                await run_rule(audit, REGISTRY["3.2.4"])
            return audit

    audit = asyncio.run(scenario())
    on_b = [f for f in audit.findings if f.page_url == "b" and f.wcag_criterion == "3.2.4"]
    assert {f.status for f in on_b} == {Status.NEEDS_REVIEW}
    assert any("no accessible name" in f.description and "/home.html" in (f.evidence or "") for f in on_b)
