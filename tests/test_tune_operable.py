"""Operable (2.x) rules: each rule that can detect a problem does so on a fixture built for it, and
quiet pages don't get findings they can't support."""

import asyncio
import functools
import http.server
import threading

import pytest

from audit_helpers import FIXTURES, found, judge

from agent_access.agent_tools.browser_tools import load_html_content, open_url
from agent_access.context import AuditContext
from agent_access.models import Status
from agent_access.probes import collect
from agent_access.probes.iris import iris_command


def descriptions(audit, criterion, status):
    return [f.description for f in found(audit, criterion).get(status, [])]


def test_input_modality_rules_detect_their_handlers():
    audit = judge("o_input_page.html", "2.1.4", "2.5.1", "2.5.2", "2.5.4", "2.5.6", "2.5.7")
    assert "s" in descriptions(audit, "2.1.4", Status.NEEDS_REVIEW)[0]   # single-key shortcut
    assert any("#swipe" in f.selectors for f in found(audit, "2.5.1")[Status.NEEDS_REVIEW])
    press = found(audit, "2.5.2")[Status.FAIL][0]                       # acts on mouse-down
    assert "#press" in press.selectors
    assert found(audit, "2.5.4").get(Status.NEEDS_REVIEW)                 # devicemotion listener
    assert "#tap" in found(audit, "2.5.6")[Status.NEEDS_REVIEW][0].selectors
    assert "#drag" in found(audit, "2.5.7")[Status.NEEDS_REVIEW][0].selectors


def test_flashing_is_never_a_pass():
    # Fails when the sampler is fast enough; under load (other Chromium runs) it may sample too slowly to see the
    # flashes, and then the rules must say so instead of passing.
    audit = judge("o_flash_page.html", "2.3.1", "2.3.2")
    for criterion in ("2.3.1", "2.3.2"):
        statuses = found(audit, criterion)
        assert Status.PASS not in statuses, criterion
        assert statuses, criterion


def test_sampled_frames_never_pass_flashing():
    quiet = {"frames": 20, "flashes_per_second": [0.0] * 4, "cell_area_px": 100_000}
    for criterion in ("2.3.1", "2.3.2"):
        slow = _rule_on(criterion, flashing={**quiet, "fps": 4.0})
        assert {f.status for f in slow} == {Status.NEEDS_REVIEW} and "too slow" in slow[0].description, criterion
        fast = _rule_on(criterion, flashing={**quiet, "fps": 12.0})
        assert {f.status for f in fast} == {Status.NEEDS_REVIEW} and "sampled frames" in fast[0].description, criterion


def test_saturated_red_flashes_fail_and_are_measured_by_wcags_formula():
    """WCAG's red flash: opposing changes of more than 20 in (R-G-B)x320 with either state saturated red. Red against
    a grey of equal luminance is no general flash, so before this only the luminance test ran and it passed silently."""
    from agent_access.probes.layout import _flash_count, red_value
    assert red_value((255, 0, 0)) == (320.0, True) and red_value((128, 128, 128)) == (0.0, False)
    assert red_value((255, 100, 0))[1]   # saturated once linearised (0.887), as WCAG computes it (not 0.72 raw)
    frames = [(255, 0, 0), (128, 128, 128)] * 5
    values, sat = zip(*map(red_value, frames))
    assert _flash_count(values, lambda j, a, b: abs(b - a) > 20 and (sat[j] or sat[j + 1])) == 4.5
    base = {"frames": 20, "fps": 12.0, "flashes_per_second": [0.0] * 4, "cell_area_px": 30_000}
    big = _rule_on("2.3.1", flashing={**base, "red_flashes_per_second": [5.0, 5.0, 0.0, 0.0]})
    assert any(f.status is Status.FAIL and "red flash" in f.description for f in big)
    small = _rule_on("2.3.1", flashing={**base, "cell_area_px": 1_000, "red_flashes_per_second": [5.0, 0.0, 0.0, 0.0]})
    assert {f.status for f in small} == {Status.NEEDS_REVIEW}
    assert {f.status for f in _rule_on("2.3.2", flashing={**base, "red_flashes_per_second": [5.0] * 4})} == {Status.FAIL}


def test_iris_only_adds_fails_to_the_sampler():
    """EA IRIS, when installed, catches flicker too fast for the sampler; it averages the frame's luminance, so it
    misses small flashes the sampler's grid sees, and its silence never overrides the sampler."""
    base = {"frames": 20, "fps": 12.0, "flashes_per_second": [0.0] * 4, "cell_area_px": 30_000}   # sampler: quiet
    iris = {"seconds": 3, "fps": 30, "painted_frames": 90, "luminance_fail_frames": 61, "red_fail_frames": 0}
    for criterion in ("2.3.1", "2.3.2"):
        flicker = _rule_on(criterion, flashing={**base, "iris": {**iris, "results": ["LuminanceFlashFailure"]}})
        assert any(f.status is Status.FAIL and f.description.startswith("EA IRIS") for f in flicker), criterion
        small = _rule_on(criterion, flashing={**base, "flashes_per_second": [5.0] * 4,
                                              "iris": {**iris, "results": [], "luminance_fail_frames": 0}})
        assert any(f.status is Status.FAIL for f in small), criterion   # the sampler's fail stands
        broken = _rule_on(criterion, flashing={**base, "iris": {"error": "IRIS couldn't run: CalledProcessError"}})
        assert Status.FAIL not in {f.status for f in broken}, criterion


@pytest.mark.skipif(not iris_command(), reason="EA IRIS (IrisApp) and ffmpeg aren't installed")
def test_the_real_iris_fails_a_flashing_page():
    audit = judge("o_flash_page.html", "2.3.1", "2.3.2")
    for criterion in ("2.3.1", "2.3.2"):
        assert any(f.description.startswith("EA IRIS") for f in found(audit, criterion).get(Status.FAIL, [])), criterion


def test_time_limits_and_interruptions_are_flagged():
    audit = judge("o_timing_page.html", "2.2.1", "2.2.3", "2.2.4")
    for criterion in ("2.2.1", "2.2.3", "2.2.4"):
        assert found(audit, criterion).get(Status.NEEDS_REVIEW), criterion
    assert "dialog" in descriptions(audit, "2.2.4", Status.NEEDS_REVIEW)[0]


def test_a_faked_clock_shows_what_an_idle_page_does():
    """Left idle on a faked clock, a page warns at 25 minutes (with a control to stay signed in), then signs out at
    30 minutes with a dialog and a navigation. A page with no timers has nothing to report in 20 hours."""
    script = ("const start = Date.now(), make = (tag, text) => Object.assign(document.createElement(tag), { textContent: text });"
              "setTimeout(() => document.querySelector('#w').append(make('p', 'Your session will expire in 5 minutes.'), "
              "make('button', 'Stay signed in')), 25 * 60000);"
              "setInterval(() => { if (Date.now() - start > 30 * 60000) { alert('Signed out'); location.hash = 'out'; } }, 1000);")
    page = '<html lang="en"><title>Account</title><main><h1>Account</h1><div id="w"></div>{}</main></html>'
    audit = judge(page.format(f"<script>{script}</script>"), "2.2.1", "2.2.5", "2.2.6")
    evidence = found(audit, "2.2.1")[Status.NEEDS_REVIEW][0].evidence
    assert evidence.startswith("after 25 min idle: text 'Your session will expire in 5 minutes.'; controls Stay signed in; "
                               "after 30 min idle: dialog 'Signed out'; went to about:blank#out")
    assert "went to" in found(audit, "2.2.5")[Status.NEEDS_REVIEW][0].evidence
    # An idle sign-out navigation is blocked (the cookies are the run's) and still reported.
    logout = judge(page.format("<script>setTimeout(() => { location.href = 'http://127.0.0.1:9/logout'; }, 10 * 60000)</script>"), "2.2.1")
    assert "after 10 min idle: went to http://127.0.0.1:9/logout (blocked)" in found(logout, "2.2.1")[Status.NEEDS_REVIEW][0].evidence
    quiet = judge(page.format(""), "2.2.1")
    assert "nothing happened in 20 hours" in found(quiet, "2.2.1")[Status.PASS][0].description


def test_a_countdown_ticking_each_second_that_takes_the_form_away_is_a_time_limit():
    """The eval's quiz_1: a 60-second countdown ticking every second replaces the answers with "Time is up. Answers
    sent.", with no request and no navigation. Jumps fired that timer once each and never reached zero (a measured
    pass); the first 5 minutes now run every tick."""
    html = ('<html lang="en"><title>Quiz</title><main><h1>Quiz</h1><form id="q"><label>Answer <input name="a"></label>'
            '<label>Other <input name="b"></label></form><p id="clock" aria-live="polite"></p></main><script>let left = 60;'
            'const tick = setInterval(() => { left -= 1; document.getElementById("clock").textContent = left + " seconds left";'
            ' if (left <= 0) { clearInterval(tick); document.getElementById("q").replaceWith(Object.assign('
            'document.createElement("p"), { textContent: "Time is up. Answers sent." })); } }, 1000);</script></html>')
    review = found(judge(html, "2.2.1"), "2.2.1")[Status.NEEDS_REVIEW][0]
    assert review.evidence.startswith("after 5 min idle: text 'Time is up. Answers sent.'; 2 form field(s) gone")


def test_navigation_aids_pass_when_present():
    audit = judge("o_ways_page.html", "2.4.4", "2.4.5", "2.4.8")
    assert "navigation, search" in descriptions(audit, "2.4.5", Status.PASS)[0]
    assert found(audit, "2.4.8").get(Status.PASS)
    assert any("vague" in d for d in descriptions(audit, "2.4.4", Status.PASS))


def test_focus_indicators_are_measured_in_pixels():
    """Each Tab stop is photographed focused and blurred. 2.4.7: a white outline on a white page changes the styles
    but no pixel, so it fails like no outline at all. 2.4.13: a 1px pale ring is too weak at 3:1; a 2px dark outline
    and a 3px box-shadow ring (which the outline check couldn't size) reach a 2px perimeter's area."""
    rings = {"dark": "outline: 2px solid #000", "pale": "outline: 1px solid #ddd", "shadow": "outline: none; box-shadow: 0 0 0 3px #1a4fd0",
             "none": "outline: none", "white": "outline: 2px solid #fff", "offset": "outline: 2px solid #000; outline-offset: 16px"}
    html = ('<html lang="en"><title>Links</title><style>body { background: #fff } a { display: inline-block; margin: 20px; '
            'padding: 8px; color: #000 }' + "".join(f" #{k}:focus {{ {v} }}" for k, v in rings.items()) + '</style><main><h1>Links</h1>'
            + "".join(f'<a id="{k}" href="#{k}">{k.title()}</a>' for k in rings) + "</main></html>")
    audit = judge(html, "2.4.7", "2.4.13")
    assert {s for f in found(audit, "2.4.7")[Status.FAIL] for s in f.selectors} == {"#none", "#white"}
    weak = found(audit, "2.4.13")[Status.FAIL][0].evidence
    assert "#pale: 0px change at 3:1" in weak and "#dark" not in weak and "#shadow" not in weak


def test_quiet_pages_get_no_unsupported_findings():
    # good_page has no video or animation, so 2.3.1 asks only to confirm the sampling (or, under load, says it was too
    # slow); it never fails and is never silent.
    good = judge("good_page.html", "2.3.1", "2.4.5", "2.5.1")
    flash = found(good, "2.3.1")
    assert set(flash) == {Status.NEEDS_REVIEW}
    assert all("too slow" in f.description or "sampled frames" in f.description for f in flash[Status.NEEDS_REVIEW])
    assert not any(d.startswith("Only no") for d in descriptions(good, "2.4.5", Status.NEEDS_REVIEW))
    # 2.5.1 counts page elements only; listeners on window/document are reported separately.
    assert not any("element(s)" in d for d in descriptions(good, "2.5.1", Status.NEEDS_REVIEW))
    # bad_page has no links, so 2.4.4 is not applicable rather than an empty "needs review".
    bad = judge("bad_page.html", "2.4.4")
    assert set(found(bad, "2.4.4")) == {Status.NOT_APPLICABLE}


def test_interactive_probes_restore_pages_loaded_by_url():
    # Restoring a URL page reloads it; inside network_guard that navigation is blocked (ERR_ABORTED), which broke
    # the disclosures, shortcuts, and pointer probes on every real website. Inline fixtures never navigate, so
    # this serves the fixtures over HTTP.
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(FIXTURES))
    handler.log_message = lambda *a: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            out = {}
            for page, probes in (("widget_page.html", ("disclosures",)), ("o_input_page.html", ("shortcuts", "pointer"))):
                assert "error" not in await open_url(audit, f"{base}/{page}")
                for name in probes:
                    out[name] = await collect(audit, name)
            return out

    try:
        results = asyncio.run(scenario())
    finally:
        server.shutdown()
    for name, result in results.items():
        assert result.error is None, (name, result.error)
        assert "stopped_early" not in result.data, (name, result.data["stopped_early"])
    assert len(results["disclosures"].data["results"]) >= 2   # it kept going after the first restore
    assert [t["key"] for t in results["shortcuts"].data["triggered"]] == ["s"]
    assert results["pointer"].data["results"][0]["acts_on_down"]


def test_english_phrase_lists_do_not_pass_other_languages():
    # AMBIGUOUS_LINK and COUNTDOWN only know English, so on a Japanese page they can't support a pass.
    html = ('<!doctype html><html lang="ja"><head><title>製品</title></head><body><main><h1>製品</h1>'
            '<p><a href="/a">こちら</a> <a href="/b">詳細</a></p></main></body></html>')
    audit = judge(html, "2.2.1", "2.4.4", "2.4.9")
    assert not any("vague" in d for d in descriptions(audit, "2.4.4", Status.PASS))
    assert any("English" in d for d in descriptions(audit, "2.4.4", Status.NEEDS_REVIEW))
    link_only = found(audit, "2.4.9")   # only axe's narrower identical-links check may pass
    assert Status.NEEDS_REVIEW in link_only and all(f.source == "axe" for f in link_only.get(Status.PASS, []))
    assert "English" in (found(audit, "2.2.1")[Status.PASS][0].evidence or "")


def test_vague_links_nothing_tells_apart_fail():
    """The eval's judgement_bad false pass: two "More" links, each alone in its paragraph under a heading that says
    "Section", go to different places. A different heading (H80), surrounding text, or one destination is enough."""
    def links(body):
        return descriptions(judge(f'<!doctype html><html lang="en"><head><title>Shop</title></head><body><main>'
                                  f'<h1>Shop</h1>{body}</main></body></html>', "2.4.4"), "2.4.4", Status.FAIL)
    assert any("2 'More' links go to different places" in d and "'Section'" in d for d in links(
        '<h2>Section</h2><p>Annual report.</p><p><a href="#a">More</a></p>'
        '<h2>Section</h2><p>New stores.</p><p><a href="#b">More</a></p>'))
    assert not links('<h2>Annual report</h2><p><a href="#a">More</a></p><h2>New stores</h2><p><a href="#b">More</a></p>')
    assert not links('<p>Annual report. <a href="#a">More</a></p><p>New stores. <a href="#b">More</a></p>')
    # An inline wrapper isn't the context: the paragraph or block around it still is.
    assert not links('<p>Annual report: <span><a href="#a">More</a></span></p><p>New stores: <span><a href="#b">More</a></span></p>')
    assert not links('<div>Annual report <strong><a href="#a">More</a></strong></div>'
                     '<div>New stores <strong><a href="#b">More</a></strong></div>')
    assert links('<div><span><a href="#a">More</a></span></div><div><span><a href="#b">More</a></span></div>')
    assert links('<p style="display:inline-block"><a href="#a">More</a></p><p style="display:inline-block"><a href="#b">More</a></p>')
    assert not links('<p><a href="#a">More</a></p><p><a href="#a">More</a></p>')


def test_generic_field_labels_fail_headings_and_labels():
    """The eval's settings_1 false pass: fields labelled "Value 1" and "Value 2" were passed as descriptive."""
    def fails(label):
        html = ('<!doctype html><html lang="en"><head><title>Limits</title></head><body><main><h1>Limits</h1><form action="/l">'
                f'<label for="v">{label}</label><input id="v" type="number"><button>Save</button></form></main></body></html>')
        return descriptions(judge(html, "2.4.6"), "2.4.6", Status.FAIL)
    assert fails("Value 1") and fails("Field 2:") and fails("Input")
    assert not fails("Value of the order") and not fails("Monthly limit")


def test_focus_rings_that_vanish_in_forced_colours_are_noted():
    """Forced-colours mode removes box-shadow: a ring drawn only with it may disappear in Windows High Contrast. That
    is a risk for the agent to check (a detail), not a WCAG failure on its own; an outline survives."""
    html = ('<!doctype html><html lang="en"><head><title>F</title><style>#a:focus{outline:none;box-shadow:0 0 0 3px blue}'
            '#b:focus{outline:2px solid blue}</style></head><body><main><h1>F</h1><button id="a">A</button>'
            '<button id="b">B</button></main></body></html>')
    audit = judge(html, "2.4.7")
    assert audit.rule_details[audit.current_url]["check_focus_visible"]["indicator_may_vanish_in_forced_colours"] == ["#a"]
    assert not found(audit, "2.4.7").get(Status.FAIL)


def _rule_on(criterion: str, **probe_data) -> list:
    """Run one rule on synthetic probe data (rules are pure functions over it), without a browser."""
    from agent_access.probes import ProbeResult
    from agent_access.rules import REGISTRY, Ctx

    audit = AuditContext()
    audit.current_url = "synthetic"
    tool = REGISTRY[criterion]
    ctx = Ctx(audit, tool, {name: ProbeResult(probe_data.get(name, {}), None) for name in tool.uses})
    tool.impl(ctx)
    return ctx.findings


def test_tab_walk_cut_short_by_a_frame_is_never_a_pass():
    # Real sites (theguardian.com): a consent dialog in an iframe holds focus, so the Tab walk reaches few or no stops.
    stop = {"selector": "a#one", "tag": "a", "name": "One", "text": "One", "tabindex": None, "visible": True,
            "indicator": True, "obscured": "none", "covered_by": None, "behind": "rgb(255, 255, 255)",
            "outline": {"style": "solid", "width": 2, "color": "rgb(0, 0, 0)"}}
    frame = {**stop, "selector": "#consent", "tag": "iframe", "name": "Consent"}
    walk = {"focus_removed": [], "mouse_only": [], "focus_order": [], "stuck_in_iframe": frame}
    for stops in ([stop, frame], [frame]):   # some stops before the frame, or none at all
        for criterion in ("2.4.7", "2.4.11", "2.4.12", "2.4.13"):
            findings = _rule_on(criterion, keyboard={**walk, "stops": stops})
            assert findings, criterion   # never only the "no automated evidence" placeholder
            assert {f.status for f in findings} == {Status.NEEDS_REVIEW}, (criterion, [f.description for f in findings])
            assert any("#consent" in (f.evidence or "") for f in findings), criterion


def test_a_black_or_red_focus_outline_is_an_indicator():
    """The transparent-outline check matched any colour ending in ', 0)': rgb(0, 0, 0) and rgb(255, 0, 0) too, so a
    black or red focus outline counted as none and failed 2.4.7. Only a transparent outline shows nothing."""
    rings = {"black": "#000", "red": "#f00", "clear": "transparent"}
    html = ('<html lang="en"><title>Links</title><style>a { display: inline-block; margin: 20px; color: #000 }'
            + "".join(f" #{k}:focus {{ outline: 2px solid {v} }}" for k, v in rings.items()) + '</style><main><h1>Links</h1>'
            + "".join(f'<a id="{k}" href="#{k}">{k.title()}</a>' for k in rings) + "</main></html>")
    audit = judge(html, "2.4.7")
    assert {s for f in found(audit, "2.4.7").get(Status.FAIL, []) for s in f.selectors} == {"#clear"}


def test_the_tab_walk_starts_at_the_top_after_a_click_left_focus_on_the_last_control():
    """In a state labelled after clicking the last button, Tab went on from that button, reached nothing, and ten
    keyboard criteria read 'no focus stops' there and differed from the page. A positive tabindex still goes first."""
    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            await load_html_content(audit, '<html lang="en"><body><main><a href="#a">One</a><input aria-label="Two">'
                                    '<button id="last" onclick="this.after(\'Shown.\')">Show</button>'
                                    '<button tabindex="1">Help</button></main></body></html>', "p")
            page_walk = (await collect(audit, "keyboard")).data["focus_order"]
            await (await audit.page()).click("#last")
            audit.set_current("p [shown]")
            return page_walk, (await collect(audit, "keyboard")).data["focus_order"]

    page_walk, state_walk = asyncio.run(scenario())
    assert page_walk == ["button 'Help'", "a 'One'", "input 'Two'", "button 'Show'"] and state_walk == page_walk
