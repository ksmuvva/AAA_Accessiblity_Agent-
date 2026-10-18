"""Operable (2.x) rules: each rule that can detect a problem does so on a fixture built for it, and
quiet pages don't get findings they can't support."""

import asyncio
import functools
import http.server
import threading

from audit_helpers import FIXTURES, found, judge

from agent_access.agent_tools.browser_tools import open_url
from agent_access.context import AuditContext
from agent_access.models import Status
from agent_access.probes import collect


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


def test_time_limits_and_interruptions_are_flagged():
    audit = judge("o_timing_page.html", "2.2.1", "2.2.3", "2.2.4")
    for criterion in ("2.2.1", "2.2.3", "2.2.4"):
        assert found(audit, criterion).get(Status.NEEDS_REVIEW), criterion
    assert "dialog" in descriptions(audit, "2.2.4", Status.NEEDS_REVIEW)[0]


def test_navigation_aids_pass_when_present():
    audit = judge("o_ways_page.html", "2.4.4", "2.4.5", "2.4.8")
    assert "navigation, search" in descriptions(audit, "2.4.5", Status.PASS)[0]
    assert found(audit, "2.4.8").get(Status.PASS)
    assert any("vague" in d for d in descriptions(audit, "2.4.4", Status.PASS))


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
    assert not links('<p><a href="#a">More</a></p><p><a href="#a">More</a></p>')


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
