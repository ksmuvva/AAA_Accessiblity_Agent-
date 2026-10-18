"""Keyboard probe: Tab through the whole page and record every focus stop.

For each stop: selector, tag, name, tabindex, whether focus changes its appearance, whether it's visible or
covered by other content, and its focus-outline style. Also records keyboard traps, navigation triggered by
focus, elements whose focus a script removes as soon as they get it (W3C failure F55), and clickable elements
that Tab never reaches.
"""

from __future__ import annotations

from agent_access.context import AuditContext
from agent_access.jsassets import js
from agent_access.probes import probe

MAX_TAB_STOPS = 200   # a walk cut at the cap is stopped_early: its passes cover only part of the page
# Inside an iframe, focus moves through the frame's own content while the parent document keeps reporting the
# iframe as active, so many more presses are allowed there before calling it a trap.
MAX_IFRAME_TABS = 40


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
    repeat = 0
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
