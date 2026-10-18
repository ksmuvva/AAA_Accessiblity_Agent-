"""Principle 2 - Operable: 2.1.1 to 2.5.8."""

from __future__ import annotations

import re

from agent_access.probes.contrast import contrast_ratio, parse_color
from agent_access.probes.keyboard import MAX_IFRAME_TABS
from agent_access.probes.screen_reader import spoken_name
from agent_access.rules import AXE, CCA, PW, SR, Ctx, wcag_tool
from agent_access.rules.common import (
    disclosure_groups, form_fields, inspectable_stops, listener_targets, names, no_stops, pass_walk, target_size,
    triggers,
)

AMBIGUOUS_LINK = re.compile(r"^(click here|here|read more|more|learn more|more info|details|link|this|go|continue|see more|find out more)\.?$", re.I)
COUNTDOWN = re.compile(r"\b(\d{1,2}:\d{2}\s*(remaining|left)|session (will )?(expire|time ?out)|time remaining|seconds? (left|remaining)|"
                       r"you will be (logged|signed) out)\b", re.I)
GENERIC_LABEL = re.compile(r"^\s*(value|field|input|text|item|entry|data|box|untitled|label|option)\s*#?\s*\d*\s*:?\s*$", re.I)
GENERIC_TITLE = re.compile(r"\s*(untitled|home|page|document|index|new page|default)\s*", re.I)


# ---------------------------------------------------------------- 2.1 Keyboard Accessible

def _keyboard_operable(ctx: Ctx) -> None:
    kb = ctx.data("keyboard")
    if not kb:
        return
    stops, removed, mouse_only = kb["stops"], kb["focus_removed"], kb["mouse_only"]
    if not stops and not removed:
        ctx.review("No keyboard-focusable elements were reached with Tab; check the accessibility tree for controls that "
                   "should be focusable.")
    if removed:  # W3C failure F55: a script removes focus when it is received
        ctx.fail(f"{len(removed)} element(s) can't be used with the keyboard: a script removes focus as soon as Tab reaches them.",
                 [s["selector"] for s in removed], names(removed))
    if mouse_only:
        ctx.review(f"{len(mouse_only)} element(s) look clickable but cannot receive keyboard focus; inspect_element each: a "
                   "container that only wraps a link or button is fine, a clickable div with no keyboard equivalent is not.",
                   [m["selector"] for m in mouse_only], names(mouse_only))
    elif stops and not removed:
        pass_walk(ctx, kb, f"Tab reached {len(stops)} focusable element(s); no clickable element was unreachable by keyboard.")
    g = disclosure_groups(ctx.data("disclosures").get("results", []))
    if g["mouse_only"]:
        ctx.fail(f"{len(g['mouse_only'])} control(s) open with a mouse click but not with Enter or Space.",
                 triggers(g["mouse_only"]), names(g["mouse_only"]))
    elif g["opened"]:
        ctx.passed(f"All {len(g['opened'])} menus/disclosures/dialogs open from the keyboard.")
    focusable = {s["selector"] for s in stops}
    clicky = [t for t in listener_targets(ctx.data("instrumentation").get("listeners", {}), "click", "mousedown", "pointerdown")
              if t not in ("window", "document") and t not in focusable]
    if clicky:
        ctx.review(f"{len(clicky)} element(s) have click handlers but were never reached with Tab; "
                   "check they're reachable another way or are just containers.", clicky)


KEYBOARD_USES = ("keyboard", "disclosures", "instrumentation")


@wcag_tool("2.1.1", "check_keyboard_accessibility", domain="navigation", tech=(PW,), uses=KEYBOARD_USES)
def check_keyboard_accessibility(ctx: Ctx) -> None:
    """All functionality must be operable with the keyboard."""
    _keyboard_operable(ctx)


@wcag_tool("2.1.2", "check_keyboard_traps", domain="navigation", tech=(PW,), uses=("keyboard", "disclosures"))
def check_keyboard_traps(ctx: Ctx) -> None:
    """Focus must never get stuck."""
    kb = ctx.data("keyboard")
    if kb.get("stuck_on"):
        s = kb["stuck_on"]
        ctx.fail("Keyboard focus could not move past an element with Tab.", [s["selector"]], f"Focus stayed on {s['tag']} '{s['name']}'.")
    elif kb.get("stuck_in_iframe"):
        ctx.review(f"Focus did not leave an embedded frame after {MAX_IFRAME_TABS} Tab presses; inspect_element the frame: a "
                   "player or map with many controls is fine if Tab eventually leaves it, a cross-origin frame needs a human.",
                   [kb["stuck_in_iframe"]["selector"]])
    elif (c := kb.get("cycle")) and c["in_modal"]:
        ctx.review(f"Tab cycles inside an open modal dialog, back to {c['tag']} '{c['name']}': fine if the dialog closes "
                   "from the keyboard (Escape or a close button), a trap if it doesn't.", [c["selector"]])
    elif c:
        ctx.fail(f"Tab cycles back to {c['tag']} '{c['name']}' after {c['after']} stops instead of moving on, so focus "
                 "can't reach the rest of the page.", [c["selector"]])
    elif kb.get("stops"):
        ctx.passed(f"Tab moved through {len(kb['stops'])} stops without getting stuck.")
    stuck = disclosure_groups(ctx.data("disclosures").get("results", []))["stuck"]
    if stuck:
        ctx.review(f"{len(stuck)} dialog(s) don't close with Escape and have no obvious close button; inspect_element the "
                   "dialog for a close control or documented keys.", triggers(stuck), names(stuck))


@wcag_tool("2.1.3", "check_keyboard_accessibility_no_exception", domain="navigation", tech=(PW,),
           uses=(*KEYBOARD_USES, "page", "media"), judgement=True)
def check_keyboard_accessibility_no_exception(ctx: Ctx) -> None:
    """Like 2.1.1 with no exception for path-dependent input (drawing, drag) - judge those."""
    _keyboard_operable(ctx)
    draggables = ctx.data("page").get("draggables", [])
    if draggables or ctx.data("media").get("canvas"):
        ctx.review("Drawing/drag interfaces must also work from the keyboard at AAA.", draggables)


@wcag_tool("2.1.4", "check_character_key_shortcuts", domain="navigation", tech=(PW,), uses=("shortcuts", "instrumentation"),
           judgement=True)
def check_character_key_shortcuts(ctx: Ctx) -> None:
    """Single-key shortcuts need a way to turn off or remap them, or must only work when the component has focus."""
    sc = ctx.data("shortcuts")
    doc_keys = [t for t in listener_targets(ctx.data("instrumentation").get("listeners", {}), "keydown", "keypress")
                if t in ("window", "document")]
    if sc.get("triggered"):
        ctx.review(f"Single-key shortcuts found: {', '.join(t['key'] for t in sc['triggered'])}. Check they can be turned off "
                   "or remapped.", evidence="; ".join(f"'{t['key']}': {', '.join(t['effects'])}" for t in sc["triggered"][:6]))
    elif sc:
        ctx.passed(f"None of {len(sc['keys_tested'])} single-character keys triggered anything"
                   + (" (page listens for keys on the document; less common keys weren't tested)." if doc_keys else "."))


# ---------------------------------------------------------------- 2.2 Enough Time

def _timing(ctx: Ctx) -> tuple[list, list]:
    long_timers = [t for t in ctx.data("instrumentation").get("timers", []) if t["kind"] == "timeout" and t["ms"] >= 20_000]
    countdown = sorted({m.group(0) for m in COUNTDOWN.finditer(ctx.data("page").get("body_text", ""))})
    return long_timers, countdown


TIMING_USES = ("instrumentation", "page")


def _countdown_caveat(ctx: Ctx) -> str | None:
    if _english(ctx):
        return None
    return (f"Timers were measured, but countdown text is only recognised in English and this page is in "
            f"'{ctx.data('page')['page_lang']}'; check for a visible session or countdown warning.")


@wcag_tool("2.2.1", "check_timing_adjustable", domain="navigation", tech=(AXE, PW), uses=(*TIMING_USES, "axe"), judgement=True)
def check_timing_adjustable(ctx: Ctx) -> None:
    """Time limits must be adjustable (turn off, adjust, or extend), unless real-time or essential."""
    ctx.axe()
    long_timers, countdown = _timing(ctx)
    if long_timers or countdown:
        ctx.review("Possible time limit: check users can turn it off, adjust, or extend it.",
                   evidence="; ".join([f"timer {t['ms'] // 1000}s" for t in long_timers[:4]] + countdown[:4]))
    elif not ctx.findings:
        ctx.passed("No time limits detected (no long timers, countdowns, or meta refresh).", evidence=_countdown_caveat(ctx))


@wcag_tool("2.2.2", "check_pause_stop_hide", domain="media", tech=(PW,), uses=("media", "page", "instrumentation"), judgement=True)
def check_pause_stop_hide(ctx: Ctx) -> None:
    """Moving, blinking, scrolling, or auto-updating content needs pause/stop/hide."""
    m = ctx.data("media")
    if not m:
        return
    # Moving content that starts automatically and lasts over 5 seconds needs pause/stop/hide.
    moving_video = [v for v in m["videos"] if (v["autoplay"] or not v["paused"]) and (v["loop"] or (v["duration"] or 0) > 5)
                    and not v["controls"] and v["visible"]]
    moving_other = m["infinite_animations"] + [{"selector": s} for s in m["gifs"]]
    if moving_video:
        ctx.fail(f"{len(moving_video)} autoplaying video(s) run over 5 seconds with no controls to pause them.",
                 [v["selector"] for v in moving_video])
    if moving_other or m["marquee_or_blink"]:
        ctx.review(f"{len(moving_other) + m['marquee_or_blink']} element(s) animate indefinitely; check they can be paused "
                   "or are essential/decorative.", [x["selector"] for x in moving_other])
    elif not moving_video:
        ctx.passed("No auto-playing video, infinite animation, GIF, or marquee found.")
    carousels = ctx.data("page").get("carousels", [])
    intervals = [t for t in ctx.data("instrumentation").get("timers", []) if t["kind"] == "interval" and 1000 <= t["ms"] <= 15000]
    if carousels:
        ctx.review(f"{len(carousels)} carousel/slider(s): check for a pause control if they auto-advance."
                   + (f" {len(intervals)} repeating timer(s) suggest auto-advance." if intervals else ""), carousels)


@wcag_tool("2.2.3", "check_no_timing", domain="navigation", tech=(PW,), uses=TIMING_USES, judgement=True)
def check_no_timing(ctx: Ctx) -> None:
    """At AAA, timing must not be part of the activity at all (except real-time events and media)."""
    long_timers, countdown = _timing(ctx)
    if long_timers or countdown:
        ctx.review("Timing appears to be part of the activity.", evidence="; ".join(countdown[:4]) or f"{len(long_timers)} long timer(s)")
    else:
        ctx.passed("No timing detected.", evidence=_countdown_caveat(ctx))


@wcag_tool("2.2.4", "check_interruptions", domain="navigation", tech=(PW,), uses=("page", "instrumentation"), judgement=True)
def check_interruptions(ctx: Ctx) -> None:
    """Interruptions (alerts, updates, pop-ups) must be postponable or suppressible, except emergencies."""
    page, inst = ctx.data("page"), ctx.data("instrumentation")
    assertive = [r for r in page.get("live_regions", []) if r["politeness"] in ("assertive", "alert")]
    signals = [s for s, present in ((f"{len(page.get('open_dialogs', []))} dialog(s) open on load", page.get("open_dialogs")),
                                    (f"{len(assertive)} assertive live region(s)", assertive),
                                    (f"{inst.get('window_open_calls')} pop-up window(s) opened", inst.get("window_open_calls")))
               if present]
    if signals:
        ctx.review("Check these interruptions can be postponed or turned off: " + "; ".join(signals),
                   page.get("open_dialogs", []) + [r["selector"] for r in assertive])
    else:
        ctx.passed("No interruptions detected on load.")


@wcag_tool("2.2.5", "check_reauthentication_data_preservation", domain="forms", tech=(PW,), uses=("page",), judgement=True)
def check_reauthentication_data_preservation(ctx: Ctx) -> None:
    """After a session expires and the user signs in again, their data must be preserved."""
    passwords = ctx.data("page").get("password_fields", [])
    if not passwords:
        ctx.not_applicable("No sign-in on this page.")
    else:
        ctx.review("Check that work in progress survives re-authentication after a session timeout.", [p["selector"] for p in passwords])


@wcag_tool("2.2.6", "check_timeout_warnings", domain="forms", tech=(PW,), uses=("forms", *TIMING_USES), judgement=True)
def check_timeout_warnings(ctx: Ctx) -> None:
    """Users must be warned how long inactivity can last before data loss, unless data is kept 20+ hours."""
    if not form_fields(ctx.data("forms")):
        ctx.not_applicable("No forms that collect data.")
        return
    long_timers, countdown = _timing(ctx)
    ctx.review("Check users are told about any inactivity timeout that loses data."
               + (" Timeout signals found." if long_timers or countdown else " No timeout signals found."))


# ---------------------------------------------------------------- 2.3 Seizures and Physical Reactions

# 25% of a 10-degree visual field is about 21,824 px^2 at typical viewing distance (WCAG 2.3.1 note).
FLASH_AREA_PX = 21_824
FLASH_NOTE = "Frame sampling is limited by screenshot speed; fast flashing (above half the sampling rate) can be missed."
# 3 flashes/s is a 3 Hz on/off signal; sampling must run above twice that (Nyquist) to see it, or a pass is unsupported.
MIN_FLASH_FPS = 7


def _flashing(ctx: Ctx) -> dict | None:
    fl = ctx.data("flashing")
    if not fl:
        return None
    over = [f for f in fl["flashes_per_second"] if f > 3]
    return {**fl, "over_three": len(over), "max": round(max(fl["flashes_per_second"], default=0), 2),
            "area": round(len(over) * fl["cell_area_px"]), "resolvable": fl["fps"] >= MIN_FLASH_FPS}


def _too_slow(fl: dict) -> str:
    return (f"Sampling too slow ({fl['fps']} fps, {MIN_FLASH_FPS}+ needed) to measure flashes above 3/s; "
            "check the page for flashing content.")


def _moving_content(m: dict) -> list[str]:
    """Video, players, canvas, GIFs, and endless animations: content that can flash after the sampled frames."""
    return [x["selector"] if isinstance(x, dict) else x
            for key in ("videos", "player_iframes", "infinite_animations") for x in m.get(key, [])] + m.get("gifs", []) \
        + (["canvas"] if m.get("canvas") else [])


SAMPLED_NOTE = ("No flashing above 3 per second in the sampled frames; sampling can't prove there is none, so confirm "
                "nothing flashes later or between frames.")


@wcag_tool("2.3.1", "check_flash_thresholds", domain="media", tech=(PW,), uses=("flashing", "media"))
def check_flash_thresholds(ctx: Ctx) -> None:
    """Nothing may flash more than 3 times a second unless below the general and red flash thresholds."""
    if (fl := _flashing(ctx)) is None:
        return
    ev = f"{fl['frames']} frames at {fl['fps']} fps; max {fl['max']} flashes/s; flashing area {fl['area']}px^2 " \
         f"(threshold {FLASH_AREA_PX}px^2). {FLASH_NOTE}"
    if fl["over_three"] and fl["area"] >= FLASH_AREA_PX:
        ctx.fail("Content flashes more than 3 times a second over a large area.", evidence=ev)
    elif fl["over_three"]:
        ctx.review("A small area flashes more than 3 times a second; check it is below the general and red flash thresholds.", evidence=ev)
    else:  # sampling sees the first seconds and can miss frames (under load), so it never proves there is no flashing
        ctx.review(SAMPLED_NOTE if fl["resolvable"] else _too_slow(fl), evidence=ev)
    # Frame sampling sees only the first seconds and can't judge red flashes; only moving content can do either.
    if moving := _moving_content(ctx.data("media")):
        ctx.review(f"{len(moving)} video/animated element(s) can flash later or in saturated red, which frame sampling "
                   "can't assess; check them.", moving)


@wcag_tool("2.3.2", "check_three_flashes", domain="media", tech=(PW,), uses=("flashing",))
def check_three_flashes(ctx: Ctx) -> None:
    """At AAA, nothing may flash more than 3 times in any second, whatever its size."""
    if (fl := _flashing(ctx)) is None:
        return
    if fl["over_three"]:
        ctx.fail(f"Content flashes {fl['max']} times a second.", evidence=FLASH_NOTE)
    else:
        ctx.review(SAMPLED_NOTE if fl["resolvable"] else _too_slow(fl), evidence=FLASH_NOTE)


@wcag_tool("2.3.3", "check_animation_from_interactions", domain="media", tech=(PW,), uses=("motion",), judgement=True)
def check_animation_from_interactions(ctx: Ctx) -> None:
    """Motion triggered by interaction must be disableable (e.g. honour prefers-reduced-motion)."""
    mo = ctx.data("motion")
    if not mo:
        return
    if mo["reduced_motion_rules"] or (mo["running_animations"] and mo["running_with_reduced_motion"] < mo["running_animations"]):
        ctx.passed(f"The page responds to prefers-reduced-motion ({mo['reduced_motion_rules']} rule(s)).")
    elif mo["running_animations"]:
        ctx.review(f"{mo['running_animations']} animation(s) run and nothing changes with reduced motion; "
                   "check interaction animations can be turned off.")
    else:
        ctx.review("No running animations at rest; check animations triggered by scrolling or clicking can be disabled.")


# ---------------------------------------------------------------- 2.4 Navigable

@wcag_tool("2.4.1", "check_bypass_blocks", domain="navigation", tech=(AXE, PW), uses=("axe", "skip_link", "page"), judgement=True)
def check_bypass_blocks(ctx: Ctx) -> None:
    """There must be a way to skip repeated blocks (skip link, landmarks, or headings)."""
    ctx.axe()
    sk, page = ctx.data("skip_link"), ctx.data("page")
    if link := sk.get("skip_link"):
        if sk.get("target_exists") and (sk.get("focus_in_target") or sk.get("target_in_view")):
            if sk.get("lands_on_main", True):
                ctx.passed(f"Skip link '{link['text']}' works and lands on the main content.", [link["selector"]])
            else:
                # It works, but may only skip part of the repeated content (e.g. lands before the site navigation).
                ctx.review(f"Skip link '{link['text']}' works, but its target ({link['target']}) isn't the main landmark "
                           "or main heading; check it skips all repeated blocks.", [link["selector"]])
        else:
            ctx.fail(f"Skip link '{link['text']}' doesn't move to its target.", [link["selector"]],
                     f"target {link['target']} exists: {sk.get('target_exists')}")
    elif page.get("landmarks", {}).get("main"):
        ctx.passed("No skip link, but a main landmark lets screen-reader users bypass repeated content.")
    elif page.get("heading_count"):
        ctx.review("No skip link or main landmark; check headings are enough to bypass repeated content.")
    else:
        ctx.fail("No skip link, landmarks, or headings to bypass repeated content.")


@wcag_tool("2.4.2", "check_page_title", domain="lead", tech=(AXE, PW), uses=("axe", "page"), judgement=True)
def check_page_title(ctx: Ctx) -> None:
    """Judge whether the page title describes the page's topic or purpose."""
    ctx.axe()
    title = ctx.data("page").get("title", "")
    if title and GENERIC_TITLE.fullmatch(title):
        ctx.fail(f"The title '{title}' is generic: it doesn't describe the page's topic or purpose (WCAG failure F25).")
    elif title:
        ctx.review(f"Check the title '{title}' describes this page.")


@wcag_tool("2.4.3", "check_focus_order", domain="navigation", tech=(PW, SR), uses=("keyboard", "disclosures"), judgement=True)
def check_focus_order(ctx: Ctx) -> None:
    """Judge whether the Tab order preserves meaning; dialogs must manage focus."""
    kb = ctx.data("keyboard")
    positive = [s for s in kb.get("stops", []) if (s["tabindex"] or "").lstrip("-").isdigit() and int(s["tabindex"]) > 0]
    if positive:
        ctx.review(f"{len(positive)} element(s) use a positive tabindex, which often breaks focus order.", [s["selector"] for s in positive])
    g = disclosure_groups(ctx.data("disclosures").get("results", []))
    if g["focus_not_moved"]:
        ctx.fail(f"{len(g['focus_not_moved'])} dialog(s) open without moving focus into them.",
                 triggers(g["focus_not_moved"]), names(g["focus_not_moved"]))
    if g["escapes_modal"]:
        ctx.review(f"Focus leaves {len(g['escapes_modal'])} modal dialog(s) while they are open.",
                   triggers(g["escapes_modal"]), names(g["escapes_modal"]))
    if g["focus_lost"]:
        ctx.review(f"After closing {len(g['focus_lost'])} dialog(s), focus doesn't return to the button that opened it.",
                   triggers(g["focus_lost"]), names(g["focus_lost"]))
    if g["dialogs"] and not (g["focus_not_moved"] or g["escapes_modal"] or g["focus_lost"]):
        ctx.passed(f"{len(g['dialogs'])} dialog(s) manage focus correctly.")
    ctx.review("Check the focus order follows the visual and logical order.", evidence=" > ".join(kb.get("focus_order", [])[:30]))
    ctx.detail("focus_order", kb.get("focus_order", []))


def _vague_links(ctx: Ctx) -> list[dict]:
    return [l for l in ctx.data("page").get("links", []) if AMBIGUOUS_LINK.match((l["aria_label"] or l["text"] or "").strip())]


def _english(ctx: Ctx) -> bool:
    """AMBIGUOUS_LINK and COUNTDOWN only know English phrases. A page without lang counts as English (3.1.1 fails it)."""
    return (ctx.data("page").get("page_lang") or "en").lower().startswith("en")


ENGLISH_ONLY = "The vague-text check only recognises English phrases ('click here', 'read more')"


@wcag_tool("2.4.4", "check_link_purpose_in_context", domain="lead", tech=(AXE, PW, SR), uses=("axe", "page", "screen_reader"),
           judgement=True)
def check_link_purpose_in_context(ctx: Ctx) -> None:
    """Judge whether each link's purpose is clear from its text plus its context (sentence, list item, heading)."""
    ctx.axe()
    links = ctx.data("page").get("links", [])
    # Vague links to different places that nothing tells apart: each alone in its element, under the same heading.
    same = {}
    for l in links:
        if l.get("bare") and AMBIGUOUS_LINK.match(l["name"]):
            same.setdefault((l["name"].lower(), l["heading"]), []).append(l)
    for group in [g for g in same.values() if len({l["href"] for l in g}) > 1]:
        ctx.fail(f"{len(group)} '{group[0]['name']}' links go to different places, and nothing in their context tells "
                 "them apart: each is alone in its element, " + (f"under the same heading '{group[0]['heading']}'."
                                                                 if group[0]["heading"] else "with no heading before it.")
                 + " WCAG counts only a link's sentence, paragraph, list item, table cell or preceding heading as its "
                   "context, not a nearby paragraph.",
                 [l["selector"] for l in group], "; ".join(l["href"] for l in group[:5]))
    if vague := _vague_links(ctx):
        ctx.review(f"{len(vague)} link(s) have vague text; check the surrounding context makes them clear.",
                   [l["selector"] for l in vague], "; ".join(f"'{l['text']}' in '{l['context'][:60]}'" for l in vague[:5]))
    elif links and _english(ctx):
        ctx.passed(f"None of {len(links)} link(s) use vague text such as 'click here' or 'read more'.",
                   evidence="; ".join(f"'{l['text']}'" for l in links[:8]))
    elif links:
        ctx.review(f"{ENGLISH_ONLY}, and this page is in '{ctx.data('page')['page_lang']}'; check that the text of its "
                   f"{len(links)} link(s) makes their purpose clear in context.", evidence="; ".join(f"'{l['text']}'" for l in links[:8]))
    elif not ctx.findings:
        ctx.not_applicable("No links on the page.")
    ctx.detail("links_announced", [f["spoken"] for f in ctx.data("screen_reader").get("focus", []) if "link" in (f["spoken"] or "")])


@wcag_tool("2.4.5", "check_multiple_navigation_ways", domain="navigation", tech=(PW,), uses=("nav",), judgement=True)
def check_multiple_navigation_ways(ctx: Ctx) -> None:
    """More than one way to find pages (nav, search, site map, index), except process steps."""
    nav = ctx.data("nav")
    ways = [w for w, ok in (("navigation", bool(nav.get("primary_nav") or nav.get("header_links"))), ("search", nav.get("search")),
                            ("site map", nav.get("sitemap_link"))) if ok]
    if len(ways) >= 2:
        ctx.passed(f"More than one way to find pages: {', '.join(ways)}.")
    else:
        ctx.review((f"Only one way to find pages detected ({ways[0]})" if ways else "No way to find other pages detected")
                   + "; check for others (related links, index, search elsewhere), unless this page is a step in a process.")


@wcag_tool("2.4.6", "check_headings_and_labels", domain="lead", tech=(AXE, SR), uses=("axe", "screen_reader", "forms"),
           judgement=True)
def check_headings_and_labels(ctx: Ctx) -> None:
    """Judge whether headings and labels describe their topic or purpose."""
    ctx.axe()
    labels = [f["label"] for f in form_fields(ctx.data("forms")) if f["label"]]
    # "Value 1", "Field 2": a generic word and a number name no purpose (the eval's settings_1 false pass).
    if generic := [f for f in form_fields(ctx.data("forms")) if GENERIC_LABEL.match(f["label"] or "")]:
        ctx.fail(f"{len(generic)} field label(s) are only a generic word and a number, so they don't say what the "
                 "field is for.", [f["selector"] for f in generic], "; ".join(f"'{f['label']}'" for f in generic[:5]))
    ctx.review("Check these headings and labels are descriptive.",
               evidence="Headings: " + " | ".join(ctx.data("screen_reader").get("headings", [])[:15])
               + (" / Labels: " + " | ".join(labels[:10]) if labels else ""))


@wcag_tool("2.4.7", "check_focus_visible", domain="navigation", tech=(PW,), uses=("keyboard",))
def check_focus_visible(ctx: Ctx) -> None:
    """Every focusable element needs a visible focus indicator."""
    kb = ctx.data("keyboard")
    if not kb:
        return
    stops, removed = inspectable_stops(kb), kb["focus_removed"]
    frames = [s for s in kb["stops"] if s["tag"] == "iframe"]
    no_indicator = [s for s in stops if s["visible"] and not s["indicator"]]
    hidden = [s for s in stops if not s["visible"]]
    if removed:  # F55
        ctx.fail(f"{len(removed)} element(s) lose focus as soon as they receive it (a script removes it), "
                 "so there is never a visible focus indicator.", [s["selector"] for s in removed], names(removed))
    if no_indicator:
        ctx.fail(f"{len(no_indicator)} focusable element(s) show no visible change when focused.",
                 [s["selector"] for s in no_indicator], "; ".join(f"{s['tag']} '{s['name']}'" for s in no_indicator[:5]))
    elif stops and not removed:
        pass_walk(ctx, kb, f"All {len(stops)} focus stops change appearance when focused.")
    elif not stops and not removed:
        no_stops(ctx, kb)
    if frames:
        ctx.review(f"Focus visibility inside {len(frames)} embedded frame(s) could not be checked automatically; a same-origin "
                   "frame can be inspected, a cross-origin one needs a human.", [s["selector"] for s in frames])
    if hidden:
        ctx.review(f"{len(hidden)} element(s) receive focus while invisible or off-screen; inspect_element each: fine if it "
                   "appears on focus (a skip link), a failure if it never becomes visible.", [s["selector"] for s in hidden])


@wcag_tool("2.4.8", "check_location_information", domain="navigation", tech=(PW,), uses=("page",), judgement=True)
def check_location_information(ctx: Ctx) -> None:
    """Users can tell where they are in a set of pages (breadcrumb, current-page indicator, site map)."""
    page = ctx.data("page")
    if page.get("breadcrumb") or page.get("aria_current"):
        ctx.passed("Location is indicated" + (" by a breadcrumb" if page.get("breadcrumb") else "")
                   + (f" and aria-current ('{page['aria_current'][0]}')" if page.get("aria_current") else "") + ".")
    else:
        ctx.review("No breadcrumb or current-page indicator found; check users can tell where they are.")


@wcag_tool("2.4.9", "check_link_purpose_link_only", domain="lead", tech=(PW,), uses=("page",))
def check_link_purpose_link_only(ctx: Ctx) -> None:
    """At AAA, each link's text alone must identify its purpose."""
    links = ctx.data("page").get("links", [])
    # A link with no text at all (an image link without alt text, F89) has no link text to identify its purpose.
    nameless = [l for l in links if not l.get("name", l["text"])]
    if nameless:
        ctx.fail(f"{len(nameless)} link(s) have no text at all (e.g. an image without alt text), so the link text "
                 "can't identify their purpose.", [l["selector"] for l in nameless],
                 "; ".join(l["href"][-60:] for l in nameless[:6]))
    if vague := _vague_links(ctx):
        ctx.fail(f"{len(vague)} link(s) don't make sense out of context.", [l["selector"] for l in vague],
                 "; ".join(f"'{l['text']}'" for l in vague[:6]))
    elif not links:
        ctx.not_applicable("No links on the page.")
    elif not nameless and _english(ctx):
        ctx.passed(f"None of {len(links)} links use vague text, and all have text.")
    elif not nameless:
        ctx.review(f"All {len(links)} links have text. {ENGLISH_ONLY}, and this page is in "
                   f"'{ctx.data('page')['page_lang']}'; check each link's text identifies its purpose on its own.")


@wcag_tool("2.4.10", "check_section_headings", domain="lead", tech=(PW,), uses=("page",), judgement=True)
def check_section_headings(ctx: Ctx) -> None:
    """At AAA, content should be organised with section headings."""
    page = ctx.data("page")
    words, headings = page.get("word_count", 0), page.get("heading_count", 0)
    fake = page.get("fake_headings", [])
    if words < 150:
        ctx.not_applicable("Not enough content to need section headings.")
    elif fake:
        # Sections whose headings are only styled text aren't organised by (programmatic) headings.
        ctx.review(f"{len(fake)} piece(s) of text look like section headings but aren't marked up as headings "
                   f"({headings} real heading(s) for about {words} words).", [f["selector"] for f in fake],
                   "; ".join(f"'{f['text']}'" for f in fake[:6]))
    elif headings < 2 and words > 300:
        ctx.review(f"About {words} words with only {headings} heading(s), so the content isn't divided into sections; "
                   "check whether it has sections that need headings.")
    elif words / (headings + 1) > 400:
        ctx.review(f"About {words} words with {headings} heading(s); check long sections have headings.")
    else:
        ctx.passed(f"{headings} heading(s) organise about {words} words.")


@wcag_tool("2.4.11", "check_focus_not_obscured_minimum", domain="navigation", tech=(PW,), uses=("keyboard",))
def check_focus_not_obscured_minimum(ctx: Ctx) -> None:
    """A focused element must not be entirely hidden by other content (sticky headers, banners)."""
    stops = inspectable_stops(ctx.data("keyboard"))
    obscured = [s for s in stops if s["obscured"] == "full"]
    if obscured:
        ctx.fail(f"{len(obscured)} focused element(s) are entirely hidden by other content.", [s["selector"] for s in obscured],
                 "; ".join(f"{s['selector']} covered by {s['covered_by']}" for s in obscured[:5]))
    elif stops:
        pass_walk(ctx, ctx.data("keyboard"), "No focused element was entirely obscured.")
    else:
        no_stops(ctx, ctx.data("keyboard"))


@wcag_tool("2.4.12", "check_focus_not_obscured_enhanced", domain="navigation", tech=(PW,), uses=("keyboard",))
def check_focus_not_obscured_enhanced(ctx: Ctx) -> None:
    """At AAA, no part of the focused element may be hidden."""
    stops = inspectable_stops(ctx.data("keyboard"))
    hidden = [s for s in stops if s["obscured"] in ("partial", "full")]
    if hidden:
        ctx.fail(f"{len(hidden)} focused element(s) are partly or fully hidden.", [s["selector"] for s in hidden],
                 "; ".join(f"{s['selector']} ({s['obscured']}, under {s['covered_by']})" for s in hidden[:5]))
    elif stops:
        pass_walk(ctx, ctx.data("keyboard"), f"All {len(stops)} focused elements were fully visible.")
    else:
        no_stops(ctx, ctx.data("keyboard"))


@wcag_tool("2.4.13", "check_focus_appearance", domain="navigation", tech=(PW, CCA), uses=("keyboard",), judgement=True)
def check_focus_appearance(ctx: Ctx) -> None:
    """At AAA, the focus indicator must be at least a 2px perimeter with 3:1 contrast between focused and unfocused."""
    stops = inspectable_stops(ctx.data("keyboard"))
    weak, unknown = [], []
    for s in stops:
        o, behind, color = s.get("outline", {}), parse_color(s.get("behind")), parse_color(s.get("outline", {}).get("color"))
        if o.get("style") not in (None, "none") and o.get("width", 0) >= 2 and color and behind:
            if (ratio := contrast_ratio(color[:3], behind[:3])) < 3:
                weak.append(f"{s['selector']}: {o['width']}px outline at {ratio}:1")
        elif not s.get("indicator"):
            weak.append(f"{s['selector']}: no focus indicator")
        else:
            unknown.append(s["selector"])
    if weak:
        ctx.fail(f"{len(weak)} focus indicator(s) are thinner than 2px or below 3:1.", evidence="; ".join(weak[:6]))
    if unknown:
        ctx.review(f"{len(unknown)} element(s) show focus by other means (shadow, colour, thin outline); check size and 3:1 contrast.", unknown)
    if stops and not weak and not unknown:
        pass_walk(ctx, ctx.data("keyboard"), f"All {len(stops)} focus indicators are 2px+ outlines with 3:1 contrast.")
    elif not stops:
        no_stops(ctx, ctx.data("keyboard"))


# ---------------------------------------------------------------- 2.5 Input Modalities

@wcag_tool("2.5.1", "check_pointer_gestures", domain="navigation", tech=(PW,), uses=("instrumentation",), judgement=True)
def check_pointer_gestures(ctx: Ctx) -> None:
    """Multipoint or path-based gestures need a single-pointer alternative."""
    found = listener_targets(ctx.data("instrumentation").get("listeners", {}), "touchstart", "touchmove", "gesturestart", "pointermove")
    elements = [t for t in found if t not in ("window", "document")]
    if elements:
        ctx.review(f"{len(elements)} element(s) listen for touch/gesture/pointer movement; check swipe, pinch, or drag actions "
                   "also have a single tap/click alternative.", elements)
    if page_level := [t for t in found if t in ("window", "document")]:
        ctx.review(f"The page listens for touch/gesture/pointer movement on the {' and '.join(page_level)}; check for swipe "
                   "or pinch actions without a single tap/click alternative.")
    if not found:
        ctx.passed("No gesture or touch-movement handlers found.")


@wcag_tool("2.5.2", "check_pointer_cancellation", domain="navigation", tech=(PW,), uses=("pointer",))
def check_pointer_cancellation(ctx: Ctx) -> None:
    """Actions must not fire on the down-event (or must be abortable/undoable)."""
    results = ctx.data("pointer").get("results", [])
    if acting := [r for r in results if r["acts_on_down"]]:
        ctx.fail(f"{len(acting)} element(s) act on mouse-down, before the pointer is released.", [r["selector"] for r in acting],
                 "; ".join(f"{r['selector']}: {', '.join(r['acts_on_down'])}" for r in acting[:5]))
    elif results:
        ctx.passed(f"{len(results)} element(s) with down-event handlers waited for release.")
    else:
        ctx.passed("No down-event handlers found; actions use click (up-event).")


@wcag_tool("2.5.3", "check_label_in_name", domain="lead", tech=(SR, PW), uses=("screen_reader", "keyboard", "forms"))
def check_label_in_name(ctx: Ctx) -> None:
    """A control's accessible name must contain its visible label text."""
    sr = ctx.data("screen_reader")
    stops = {s["selector"]: s for s in inspectable_stops(ctx.data("keyboard"))}
    # Form fields: the visible label is their <label>; other controls: their own text.
    field_labels = {f["selector"]: f["label"] for f in form_fields(ctx.data("forms")) if f["visible_label"]}
    norm = lambda t: re.sub(r"[^\w ]+", "", t.lower()).strip()  # noqa: E731
    mismatches = []
    for f in sr.get("focus", []):
        stop = stops.get(f["selector"]) or {}
        visible = field_labels.get(f["selector"], "") if stop.get("tag") in ("input", "select", "textarea") else stop.get("text", "")
        if visible and len(visible) <= 60 and norm(visible) and norm(visible) not in norm(spoken_name(f["spoken"])):
            mismatches.append(f"visible '{visible}' vs announced '{spoken_name(f['spoken'])}'")
    if mismatches:
        ctx.fail(f"{len(mismatches)} control(s) have an accessible name that doesn't contain the visible label.",
                 evidence="; ".join(mismatches[:6]))
    elif sr.get("focus"):
        pass_walk(ctx, ctx.data("keyboard"),
                   f"Announced names contain the visible labels for {len(sr['focus'])} focusable element(s) ({sr.get('backend')}).")
    else:
        no_stops(ctx, ctx.data("keyboard"))


@wcag_tool("2.5.4", "check_motion_actuation", domain="navigation", tech=(PW,), uses=("instrumentation",), judgement=True)
def check_motion_actuation(ctx: Ctx) -> None:
    """Functions triggered by device motion need a UI alternative and a way to turn motion off."""
    if listener_targets(ctx.data("instrumentation").get("listeners", {}), "devicemotion", "deviceorientation"):
        ctx.review("The page listens for device motion/orientation; check every motion action has a button alternative and can be disabled.")
    else:
        ctx.not_applicable("The page doesn't listen for device motion or orientation.")


@wcag_tool("2.5.5", "check_target_size_enhanced", domain="navigation", tech=(PW,), uses=("targets",))
def check_target_size_enhanced(ctx: Ctx) -> None:
    """At AAA, targets must be at least 44x44px (inline links and browser-default controls exempt)."""
    target_size(ctx, 44, ctx.data("targets").get("enhanced", {}))


@wcag_tool("2.5.6", "check_concurrent_input_mechanisms", domain="navigation", tech=(PW,), uses=("instrumentation",), judgement=True)
def check_concurrent_input_mechanisms(ctx: Ctx) -> None:
    """Content must not restrict users to one input type (touch only, mouse only)."""
    listeners = ctx.data("instrumentation").get("listeners", {})
    touch_only = (set(listener_targets(listeners, "touchstart", "touchend"))
                  - set(listener_targets(listeners, "click", "mousedown", "pointerdown")) - {"window", "document"})
    if touch_only:
        ctx.review(f"{len(touch_only)} element(s) handle touch but not mouse/pointer; check they work with all inputs.", sorted(touch_only))
    else:
        ctx.passed("No touch-only handlers found.")


@wcag_tool("2.5.7", "check_dragging_alternatives", domain="navigation", tech=(PW,), uses=("page", "sliders", "instrumentation"),
           judgement=True)
def check_dragging_alternatives(ctx: Ctx) -> None:
    """Anything done by dragging must also work with a single pointer without dragging."""
    draggables = ctx.data("page").get("draggables", [])
    sliders = ctx.data("sliders").get("sliders", [])
    stuck = [s for s in sliders if s.get("keyboard_changes_value") is False]
    if stuck:
        ctx.review(f"{len(stuck)} slider(s) don't respond to arrow keys; check for a click/tap alternative to dragging.",
                   [s["selector"] for s in stuck])
    if draggables:
        ctx.review(f"{len(draggables)} draggable element(s); check there's a non-drag way to do the same thing.", draggables)
    listeners = ctx.data("instrumentation").get("listeners", {})
    pressed = listener_targets(listeners, "mousedown", "pointerdown", "touchstart")
    # Press plus move handlers (on the element or delegated to the document) are how custom drags are built.
    if drags := pressed if listener_targets(listeners, "mousemove", "pointermove", "touchmove") else []:
        ctx.review(f"Press and move handlers ({', '.join(drags[:3])}) can make a custom drag (a slider, map or sortable "
                   "list); check anything done by dragging also works with a single pointer.",
                   [t for t in drags if t not in ("window", "document")])
    if not sliders and not draggables and not drags:
        if listener_targets(listeners, "dragstart", "drop"):
            ctx.review("Drag-and-drop handlers found; check for a single-pointer alternative.")
        else:
            ctx.not_applicable("No dragging interactions found.")
    elif sliders and not stuck and not draggables and not drags:
        ctx.passed(f"{len(sliders)} slider(s) can be operated without dragging (arrow keys / click).")


@wcag_tool("2.5.8", "check_target_size_minimum", domain="navigation", tech=(PW, AXE), uses=("targets", "axe"))
def check_target_size_minimum(ctx: Ctx) -> None:
    """Targets must be at least 24x24px or have enough spacing (inline links exempt)."""
    target_size(ctx, 24, ctx.data("targets").get("minimum", {}))
    ctx.axe()
