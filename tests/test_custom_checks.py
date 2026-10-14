"""Keyboard and layout probes, judged by their rules, against fixtures with known problems (and a clean page)."""

import pytest

from audit_helpers import data, judge, judge_then, statuses
from agent_access.engine import run_rule
from agent_access.models import Status
from agent_access.rules import REGISTRY

LAYOUT_AND_KEYBOARD = ("1.4.10", "1.4.12", "2.1.1", "2.1.2", "2.4.7", "2.4.11", "2.5.8", "3.2.1")


@pytest.fixture(scope="module")
def failing():
    return judge("custom_fail_page.html", *LAYOUT_AND_KEYBOARD)


def test_failures_are_detected(failing):
    for criterion in ("1.4.12", "1.4.10", "2.5.8", "2.4.7", "2.4.11"):
        assert Status.FAIL in statuses(failing, criterion), criterion
    assert Status.NEEDS_REVIEW in statuses(failing, "2.1.1")


def test_failure_details(failing):
    stops = data(failing, "keyboard")["stops"]
    assert [s["selector"] for s in stops if s["visible"] and not s["indicator"]] == ["#no-focus-style"]
    assert [s["selector"] for s in stops if s["obscured"] == "full"][0] == "#hidden-link"
    assert {f["name"] for f in data(failing, "targets")["minimum"]["failures"]} >= {"Previous", "Next"}
    assert data(failing, "reflow")["scroll_width"] >= 700


def test_page_state_is_restored():
    async def page_state(audit):
        page = await audit.page()
        return page.viewport_size, await page.evaluate("() => document.querySelectorAll('style').length")

    _, (size, styles) = judge_then("custom_fail_page.html", "1.4.4", "1.4.10", "1.4.12", "1.3.4", then=page_state)
    assert size["width"] == 1280
    assert styles == 1  # the injected text-spacing style was removed


def test_clean_page_has_no_custom_failures():
    audit = judge("good_page.html", *LAYOUT_AND_KEYBOARD)
    assert not [f for f in audit.findings if f.status is Status.FAIL]
    order = data(audit, "keyboard")["focus_order"]
    assert order[0] == "a 'Skip to main content'"
    assert "input 'Email address'" in order


def test_keyboard_trap_is_detected():
    audit = judge("trap_page.html", "2.1.2")
    assert data(audit, "keyboard")["stuck_on"]["selector"] == "#trap"
    assert Status.FAIL in statuses(audit, "2.1.2")


def test_tabbing_through_iframe_is_not_a_trap():
    audit = judge("iframe_page.html", "2.1.2")
    kb = data(audit, "keyboard")
    assert kb["stuck_on"] is None and kb["stuck_in_iframe"] is None
    assert kb["focus_order"] == ["button 'Before frame'", "iframe 'Player'", "button 'After frame'"]
    assert statuses(audit, "2.1.2") == {Status.PASS}


def test_rerun_replaces_only_that_rule():
    async def rerun(audit):
        before = sorted((f.check_id, f.wcag_criterion, f.status) for f in audit.findings)
        await run_rule(audit, REGISTRY["2.4.7"])
        return before, sorted((f.check_id, f.wcag_criterion, f.status) for f in audit.findings)

    _, (before, after) = judge_then("custom_fail_page.html", *LAYOUT_AND_KEYBOARD, then=rerun)
    assert before == after


def test_focus_removed_by_script_does_not_end_the_tab_walk():
    audit = judge("focus_blur_page.html", "2.1.1", "2.4.7", "3.2.1")
    kb = data(audit, "keyboard")
    # Tab continues past the links that blur themselves (W3C failure F55) instead of stopping there.
    assert kb["focus_order"] == ["a 'Home'", "a 'Contact'"]
    assert [s["selector"] for s in kb["focus_removed"]] == ["#news", "#tickets"]
    for criterion in ("2.1.1", "2.4.7", "3.2.1"):
        assert statuses(audit, criterion) == {Status.FAIL}, criterion
