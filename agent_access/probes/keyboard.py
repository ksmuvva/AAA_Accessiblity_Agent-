"""Keyboard probe: Tab through the whole page and record every focus stop.

For each stop: selector, tag, name, tabindex, whether focus changes its appearance, whether it's visible or
covered by other content, its focus-outline style and, for the first MAX_PIXEL_STOPS, the indicator in pixels. Also records keyboard traps, navigation triggered by
focus, elements whose focus a script removes as soon as they get it (W3C failure F55), and clickable elements
that Tab never reaches.
"""

from __future__ import annotations

import io

from PIL import Image

from agent_access.context import AuditContext
from agent_access.jsassets import js
from agent_access.probes import probe
from agent_access.probes.contrast import contrast_ratio

MAX_TAB_STOPS = 200   # a walk cut at the cap is stopped_early: its passes cover only part of the page
# Inside an iframe, focus moves through the frame's own content while the parent document keeps reporting the
# iframe as active, so many more presses are allowed there before calling it a trap.
MAX_IFRAME_TABS = 40
MAX_PIXEL_STOPS = 30  # visible stops whose indicator is measured in pixels (two screenshots each)
FOCUS_MARGIN = 12     # CSS px around the element, where outlines, offsets and shadows are drawn


async def _focus_pixels(page, stop: dict) -> dict | None:
    """The focus indicator in pixels: the element and FOCUS_MARGIN around it (plus an outline's offset and width),
    focused then blurred (animations finished, caret hidden). WCAG 2.4.13 asks for a change of 3:1 or more over at
    least the area of a 2 CSS px perimeter of the element (4 x (width + height)). None when the element or its
    outline is partly off-screen: the pixels couldn't show the whole indicator, so the styles decide."""
    view, (x, y), size, o = page.viewport_size, stop["at"].values(), stop["size"], stop["outline"]
    reach = max(0, o["offset"] + o["width"]) if o["style"] != "none" else 0
    if min(x, y) < reach or x + size["width"] + reach > view["width"] or y + size["height"] + reach > view["height"]:
        return None
    margin = FOCUS_MARGIN + reach
    left, top = max(0, x - margin), max(0, y - margin)
    clip = {"x": left, "y": top, "width": min(view["width"], x + size["width"] + margin) - left,
            "height": min(view["height"], y + size["height"] + margin) - top}
    focused = await page.screenshot(clip=clip, animations="disabled")
    await page.evaluate(js("keyboard/focus_toggle"), "blur")
    try:
        blurred = await page.screenshot(clip=clip, animations="disabled")
    finally:
        await page.evaluate(js("keyboard/focus_toggle"), "focus")
    on, off = (Image.open(io.BytesIO(shot)).convert("RGB").tobytes() for shot in (focused, blurred))
    changed = [(tuple(on[i:i + 3]), tuple(off[i:i + 3])) for i in range(0, len(on), 3) if on[i:i + 3] != off[i:i + 3]]
    return {"changed_px": len(changed), "contrast_px": sum(contrast_ratio(a, b) >= 3 for a, b in changed),
            "min_px": 4 * (size["width"] + size["height"])}


@probe("keyboard")
async def keyboard(audit: AuditContext) -> dict:
    page = await audit.page()
    await page.evaluate(js("keyboard/reset"))
    stops: list[dict] = []
    seen: set[int] = set()
    stuck_on = stuck_in_iframe = cycle = None
    capped = False
    focus_navigations: list[dict] = []  # 3.2.1: moving focus must not navigate; the guard records attempts
    focus_removed: list[dict] = []
    repeat = measured = 0
    async with audit.network_guard() as blocked:
        for _ in range(MAX_TAB_STOPS + MAX_IFRAME_TABS):
            seen_blocked = len(blocked)
            await page.evaluate(js("keyboard/focus_log"))
            await page.keyboard.press("Tab")
            stop = await page.evaluate(js("keyboard/focus_stop"), None)
            await page.wait_for_timeout(50)
            navigations = [b for b in blocked[seen_blocked:] if b.endswith("(navigation)")]
            if stop is not None and navigations:
                focus_navigations.append({**stop, "request": navigations[0]})
            if stop is None:
                dropped = await page.evaluate(js("keyboard/dropped_focus"), None)
                if dropped is None or dropped["id"] in seen:
                    break  # Focus left the document (or wrapped around): the tab cycle is complete.
                # A script removed focus as soon as the element got it; Tab continues from that element.
                seen.add(dropped["id"])
                focus_removed.append(dropped)
                continue
            if stops and stop["id"] == stops[-1]["id"]:
                repeat += 1
                if stop["tag"] == "iframe":
                    if repeat >= MAX_IFRAME_TABS:
                        stuck_in_iframe = stop
                        break
                elif repeat >= 2:
                    stuck_on = stop
                    break
                continue
            if len(stops) >= MAX_TAB_STOPS:
                capped = True
                break
            repeat = 0
            if stop["id"] in seen:   # wrapped round to the first stop, or cycling among a few (a trap)
                if stop["id"] != stops[0]["id"]:
                    cycle = {**stop, "after": len(stops), "in_modal": await page.evaluate(
                        "() => !!document.activeElement?.closest('dialog[open], [aria-modal=\"true\"]')")}
                break
            seen.add(stop["id"])
            if measured < MAX_PIXEL_STOPS and stop["visible"] and stop["obscured"] == "none" and stop["tag"] != "iframe":
                if (pixels := await _focus_pixels(page, stop)) is not None:
                    stop["pixels"], measured = pixels, measured + 1
            stops.append(stop)
    mouse_only = await page.evaluate(js("keyboard/mouse_only"), None)
    await page.evaluate(js("keyboard/reset"))
    return {
        "stops": stops,
        "focus_order": [f"{s['tag']} '{s['name']}'" for s in stops[:40]],
        "stuck_on": stuck_on,
        "stuck_in_iframe": stuck_in_iframe,
        "cycle": cycle,
        "stopped_early": capped,
        "focus_navigations": focus_navigations,
        "focus_removed": focus_removed,
        "mouse_only": mouse_only,
    }
