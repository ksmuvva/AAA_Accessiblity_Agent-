"""Interactive probes and their rules against fixtures that contain each defect next to a correct version of the pattern."""

import asyncio

import pytest

from audit_helpers import data, fixture_html, found, judge, judge_then, statuses
from agent_access.agent_tools.browser_tools import inspect, load_html_content
from agent_access.context import AuditContext
from agent_access.engine import retest, run_site_tests, run_sweep
from agent_access.models import Status


def test_resize_text_detects_clipping_and_unscaled_text():
    audit = judge("zoom_page.html", "1.4.4")
    assert data(audit, "zoom")["newly_clipped"]
    assert len(found(audit, "1.4.4")[Status.FAIL]) == 2


def test_resize_text_clean_page_and_viewport_restored():
    async def viewport(audit):
        return (await audit.page()).viewport_size

    audit, size = judge_then("good_page.html", "1.4.4", then=viewport)
    assert statuses(audit, "1.4.4") == {Status.PASS}
    assert size == {"width": 1280, "height": 800}


def test_orientation_lock_detected():
    audit = judge("orientation_page.html", "1.3.4")
    o = data(audit, "orientation")
    assert o["portrait"]["rotate_message"] and not o["landscape"]["rotate_message"]
    assert Status.FAIL in statuses(audit, "1.3.4")
    assert statuses(judge("good_page.html", "1.3.4"), "1.3.4") == {Status.PASS}


def test_hover_content_bad_and_good_tooltips():
    audit = judge("hover_page.html", "1.4.13")
    by_trigger = {h["trigger"]: h for h in data(audit, "hover")["hover"]}
    assert by_trigger["#bad-trigger"]["hoverable"] is False
    good = by_trigger["#good-trigger"]
    assert good["hoverable"] and good["persistent"] and good["dismissible"]
    assert found(audit, "1.4.13")[Status.FAIL][0].selectors == ["#bad-trigger"]


@pytest.fixture(scope="module")
def forms():
    return judge("form_page.html", "1.3.5", "3.2.2", "3.3.1", "4.1.3")


def submission(audit, form: str, mode: str) -> dict:
    return next(s for s in data(audit, "forms")["submissions"] if s["form"] == form and s["mode"] == mode)


def test_list_forms(forms):
    by_form = {f["form_selector"]: f for f in data(forms, "forms")["forms"]}
    assert by_form["#silent"]["novalidate"] is True
    phone = next(x for x in by_form["#silent"]["fields"] if x["name"] == "phone")
    assert phone["likely_personal_without_autocomplete"] and not phone["visible_label"]
    assert next(x for x in by_form["#helpful"]["fields"])["autocomplete"] == "email"


def test_form_error_outcomes(forms):
    silent, helpful = submission(forms, "#silent", "empty"), submission(forms, "#helpful", "empty")
    assert not silent["new_text"] and not silent["state"]["alerts"] and not silent["tried_to_submit"]
    assert "Enter an email address in the format name@example.com" in [n["text"] for n in helpful["new_text"]]
    assert submission(forms, "#native", "empty")["state"]["native_invalid"][0]["selector"] == "#n-name"
    assert submission(forms, "#helpful", "invalid")["new_text"]  # "not-an-email" is rejected too

    results = found(forms, "3.3.1")
    assert any(f.selectors == ["#silent"] for f in results[Status.FAIL])
    passed = " ".join(f.description for f in results[Status.PASS])
    assert "#helpful empty" in passed and "#native empty" in passed
    assert Status.PASS in statuses(forms, "4.1.3")  # helpful form uses role="alert"


def test_form_without_required_fields_that_ignores_empty_input_needs_review():
    # A site search that ignores an empty query hasn't detected an input error, so it isn't a 3.3.1 failure.
    html = ('<html lang="en"><body><form id="search" role="search" onsubmit="return false">'
            '<label for="q">Search</label><input id="q" type="search" name="q"><button>Go</button></form></body></html>')
    assert statuses(judge(html, "3.3.1"), "3.3.1") == {Status.NEEDS_REVIEW}


def test_select_that_navigates_fails_on_input(forms):
    jump = next(c for change in data(forms, "forms")["changes"] if change["form"] == "#jump" for c in change["changes"])
    assert jump["control"] == "#country"
    assert "example.com/?site=" in jump["navigation"][0]
    assert Status.FAIL in statuses(forms, "3.2.2")


def test_form_tests_never_reach_the_network(forms):
    # The native form posts to example.com, but browser validation stops it before any request.
    assert submission(forms, "#native", "empty")["blocked_requests"] == []


def test_retest_merges_into_the_cache_and_rejudges(forms):
    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            await load_html_content(audit, fixture_html("form_page.html"))
            await retest(audit, "forms", ["#silent"])
            first = len(data(audit, "forms")["submissions"])
            out = await retest(audit, "forms", ["#silent"])
            return first, len(data(audit, "forms")["submissions"]), out

    first, second, out = asyncio.run(scenario())
    assert first == second  # the re-test replaced #silent's entries instead of adding more
    assert any(r["criterion"].startswith("3.3.1") for r in out["rules_rerun"])


def test_disclosures_and_dialogs():
    audit = judge("widget_page.html", "2.1.1", "2.4.3", "4.1.2")
    by_trigger = {x["trigger"]: x for x in data(audit, "disclosures")["results"]}
    assert by_trigger["#good-toggle"]["opened_by"] == "Enter"
    assert by_trigger["#mouse-only"]["opened_by"] == "mouse only"
    assert by_trigger["#good-dialog-btn"]["focus_moved_inside"] and by_trigger["#good-dialog-btn"]["focus_returned"]

    fails = {(f.wcag_criterion, tuple(f.selectors)) for f in audit.findings if f.status is Status.FAIL}
    assert ("2.1.1", ("#mouse-only",)) in fails
    assert ("4.1.2", ("#stale-toggle",)) in fails
    assert ("2.4.3", ("#bad-dialog-btn",)) in fails
    # The correct patterns produce no failures.
    assert not any(s in ("#good-toggle", "#good-dialog-btn") for _, sels in fails for s in sels)


def test_media_findings():
    audit = judge("media_page.html", "1.2.2", "1.4.2", "2.2.2")
    assert data(audit, "media")["infinite_animations"]
    assert [f.selectors for f in found(audit, "1.4.2")[Status.FAIL]] == [["#promo"]]
    assert found(audit, "1.2.2")[Status.NEEDS_REVIEW][0].selectors == ["#promo"]
    assert Status.FAIL in statuses(audit, "2.2.2")


def test_no_media_is_not_applicable():
    audit = judge("good_page.html", "1.2.1", "1.2.2", "1.2.5", "1.4.2")
    for c in ("1.2.1", "1.2.2", "1.2.5", "1.4.2"):
        assert statuses(audit, c) == {Status.NOT_APPLICABLE}, c


def test_navigation_consistency_across_pages():
    page_a = ('<html lang="en"><body><nav aria-label="Main"><a href="/a">Home</a><a href="/b">Shop</a><a href="/c">Help</a></nav>'
              '<input type="search" aria-label="Search"></body></html>')
    page_b = ('<html lang="en"><body><nav aria-label="Main"><a href="/b">Shop</a><a href="/a">Home</a><a href="/c">Support</a></nav>'
              '</body></html>')

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            for label, html in (("a", page_a), ("b", page_b)):
                await load_html_content(audit, html, label)
                await run_sweep(audit)
            await run_site_tests(audit)
            return audit

    audit = asyncio.run(scenario())
    by = {(f.page_url, f.wcag_criterion): f.status for f in audit.findings if f.wcag_criterion in ("3.2.3", "3.2.4")}
    assert by[("b", "3.2.3")] is Status.FAIL          # Home/Shop swapped
    assert by[("a", "3.2.4")] is Status.NEEDS_REVIEW  # /c is "Help" on one page, "Support" on the other
    # 2.4.5 (check_multiple_navigation_ways) reads these: nav + search on a, nav only on b.
    assert audit.observations["a"]["nav"].data["search"] and not audit.observations["b"]["nav"].data["search"]


def test_navigation_without_landmarks_is_compared():
    # No <nav>/<header>: the menu block is found by its shape, so 3.2.3 still has something to compare.
    def page(order):
        items = "".join(f'<li><a href="/{x.lower()}">{x}</a></li>' for x in order)
        return f'<html lang="en"><body><div id="menu"><ul>{items}</ul></div><p>Some page text here.</p></body></html>'

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            for label, order in (("a", ["Home", "News", "Tickets"]), ("b", ["News", "Home", "Tickets"])):
                await load_html_content(audit, page(order), label)
                await run_sweep(audit)
            await run_site_tests(audit)
            return audit

    audit = asyncio.run(scenario())
    assert audit.observations["a"]["nav"].data["primary_nav"] == ["Home", "News", "Tickets"]
    assert Status.FAIL in {f.status for f in audit.findings if f.page_url == "b" and f.wcag_criterion == "3.2.3"}


def test_submenu_under_the_current_item_is_not_reordering():
    # W3C BAD pattern: the current page's menu item is plain text with a submenu; other items are links.
    def page(current):
        items = "".join(f'<li>{x}<div class="subnav"><a href="/{x.lower()}/report">Report</a> '
                        f'<a href="/notes">Show Annotations</a></div></li>' if x == current
                        else f'<li><a href="/{x.lower()}">{x}</a></li>' for x in ("Home", "News", "Tickets", "Survey"))
        return f'<html lang="en"><body><div id="mnav"><ul>{items}</ul></div><p>Some page text here.</p></body></html>'

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            for label in ("Home", "Tickets"):
                await load_html_content(audit, page(label), label)
                await run_sweep(audit)
            await run_site_tests(audit)
            return audit

    audit = asyncio.run(scenario())
    assert audit.observations["Home"]["nav"].data["primary_nav"] == ["News", "Tickets", "Survey"]
    assert {f.status for f in audit.findings if f.wcag_criterion == "3.2.3"} == {Status.PASS}


def test_inspect_element_shows_lang():
    async def lang(audit):
        return await inspect(audit, "#fr")

    _, r = judge_then('<html lang="en"><body><p lang="fr" id="fr">Bonjour</p></body></html>', then=lang)
    assert r["elements"][0]["inherited_lang"] == "fr" and r["elements"][0]["attributes"]["lang"] == "fr"


def test_restore_reruns_page_scripts():
    """Regression: set_content reused the window, so scripts with top-level const failed on restore."""

    async def toggle(audit):
        await audit.restore_page()
        page = await audit.page()
        await page.click("#good-toggle")
        return await page.get_attribute("#good-toggle", "aria-expanded")

    assert judge_then("widget_page.html", then=toggle)[1] == "true"
