"""Layout probes: text spacing, reflow, target size, 200% zoom, orientation, and flashing.

Probes that resize the viewport or inject styles put the page back the way they found it.
"""

from __future__ import annotations

import io
import time

from PIL import Image

from agent_access.context import AuditContext
from agent_access.jsassets import js
from agent_access.probes import probe
from agent_access.probes.contrast import relative_luminance

TEXT_SPACING_CSS = (
    "* { line-height: 1.5 !important; letter-spacing: 0.12em !important; word-spacing: 0.16em !important; }"
    " p { margin-bottom: 2em !important; }"
)
REFLOW_VIEWPORT = {"width": 320, "height": 256}
PORTRAIT, LANDSCAPE = {"width": 390, "height": 844}, {"width": 844, "height": 390}
ZOOM_FACTOR = 2
FLASH_SECONDS = 2.0
FLASH_GRID = (40, 25)


async def _at_viewport(page, size: dict, measure, settle_ms: int = 400):
    original = page.viewport_size
    try:
        await page.set_viewport_size(size)
        await page.wait_for_timeout(settle_ms)  # let media queries and resize handlers settle
        return await measure()
    finally:
        if original:
            await page.set_viewport_size(original)


@probe("text_spacing")
async def text_spacing(audit: AuditContext) -> dict:
    """Text that becomes clipped when WCAG 1.4.12 spacing is forced (overlap isn't detected)."""
    page = await audit.page()
    before = await page.evaluate(js("layout/clipped_text"), None)
    style = await page.add_style_tag(content=TEXT_SPACING_CSS)
    try:
        await page.wait_for_timeout(200)
        after = await page.evaluate(js("layout/clipped_text"), None)
    finally:
        await style.evaluate("(el) => el.remove()")
    already = {c["id"] for c in before}
    return {"newly_clipped": [c for c in after if c["id"] not in already]}


@probe("reflow")
async def reflow(audit: AuditContext) -> dict:
    """Horizontal scrolling at 320 CSS px, and the outermost elements that cause it."""
    page = await audit.page()
    return await _at_viewport(page, REFLOW_VIEWPORT, lambda: page.evaluate(js("layout/reflow"), None), 300)


@probe("targets")
async def targets(audit: AuditContext) -> dict:
    """Undersized pointer targets: "minimum" = 24px with the spacing exception (2.5.8), "enhanced" = 44px (2.5.5)."""
    page = await audit.page()
    return {"minimum": await page.evaluate(js("layout/target_size"), None),
            "enhanced": await page.evaluate(js("layout/target_size"), {"min": 44, "spacing": False})}


@probe("zoom")
async def zoom(audit: AuditContext) -> dict:
    """Emulate 200% browser zoom by halving the viewport (the same CSS-pixel layout as real zoom)."""
    page = await audit.page()
    original = page.viewport_size
    before_clipped = await page.evaluate(js("layout/clipped_text"), None)
    before_sizes = await page.evaluate(js("layout/font_sizes"), None)
    zoomed = {"width": original["width"] // ZOOM_FACTOR, "height": original["height"] // ZOOM_FACTOR}

    async def measure():
        return (await page.evaluate(js("layout/clipped_text"), None), await page.evaluate(js("layout/font_sizes"), None),
                await page.evaluate(js("layout/hidden_overflow"), None))

    after_clipped, after_sizes, lost = await _at_viewport(page, zoomed, measure)
    already = {c["id"] for c in before_clipped}
    return {
        "zoom_viewport": zoomed,
        "newly_clipped": [c for c in after_clipped if c["id"] not in already],
        "lost_off_screen": lost,
        # Font sizes before and after for every text element present in both (the rule decides what "didn't scale" is).
        "font_sizes": [{**after_sizes[k], "before_px": before_sizes[k]["size"], "after_px": after_sizes[k]["size"]}
                       for k in after_sizes if k in before_sizes],
    }


@probe("orientation")
async def orientation(audit: AuditContext) -> dict:
    """Visible text length and any "rotate your device" message, in portrait and in landscape."""
    page = await audit.page()
    return {name: await _at_viewport(page, size, lambda: page.evaluate(js("layout/orientation")))
            for name, size in (("portrait", PORTRAIT), ("landscape", LANDSCAPE))}


@probe("flashing")
async def flashing(audit: AuditContext) -> dict:
    """Viewport frames sampled for FLASH_SECONDS: per-cell count of opposing luminance changes (2.3.x)."""
    page = await audit.page()
    cols, rows = FLASH_GRID
    frames, times = [], []
    start = time.monotonic()
    while time.monotonic() - start < FLASH_SECONDS:
        # Quality 80: a low-quality JPEG washes out saturated red.
        img = Image.open(io.BytesIO(await page.screenshot(type="jpeg", quality=80))).convert("RGB").resize((cols, rows))
        frames.append([img.getpixel((x, y)) for y in range(rows) for x in range(cols)])
        times.append(time.monotonic() - start)
    viewport = page.viewport_size or {"width": 1280, "height": 800}
    flashes, red = [], []
    for i in range(cols * rows):
        lum = [relative_luminance(f[i]) for f in frames]
        # A flash is a pair of opposing changes of >=10% relative luminance where the darker state is < 0.80.
        flashes.append(_flash_count(lum, lambda j, a, b: abs(b - a) >= 0.1 and min(a, b) < 0.8))
        # A red flash: opposing changes of more than 20 in (R-G-B)x320 where either state is saturated red.
        rv, sat = zip(*(red_value(f[i]) for f in frames)) if frames else ((), ())
        red.append(_flash_count(rv, lambda j, a, b: abs(b - a) > 20 and (sat[j] or sat[j + 1])))
    duration = times[-1] if times else FLASH_SECONDS
    return {"frames": len(frames), "fps": round(len(frames) / duration, 1) if duration else 0,
            "flashes_per_second": [f / duration for f in flashes] if duration else flashes,
            "red_flashes_per_second": [f / duration for f in red] if duration else red,
            "cell_area_px": (viewport["width"] / cols) * (viewport["height"] / rows)}


def red_value(rgb: tuple[int, int, int]) -> tuple[float, bool]:
    """WCAG's red flash measure for one colour: ((R-G-B) x 320, negative as 0) and whether it is saturated red
    (R / (R+G+B) >= 0.8), with R, G, B in 0..1."""
    r, g, b = (c / 255 for c in rgb)
    return max(0.0, (r - g - b) * 320), r + g + b > 0 and r / (r + g + b) >= 0.8


def _flash_count(series, counts) -> float:
    """Flashes in one cell's series: pairs of opposing changes that `counts(index, before, after)` accepts."""
    changes, last = 0, 0
    for j, (a, b) in enumerate(zip(series, series[1:])):
        if counts(j, a, b) and (direction := 1 if b > a else -1) != last:
            changes, last = changes + 1, direction
    return changes / 2
