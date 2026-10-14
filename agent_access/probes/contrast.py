"""Contrast probe using the Colour Contrast Analyser (CCA) method.

CCA is a desktop app with no automation interface, so this reproduces its method:
- the WCAG 2 relative-luminance contrast ratio (identical formula to CCA), and
- an "eyedropper" for text over images/gradients: the element's screenshot pixels are sampled, and the
  ratio is taken against the dominant background colours (worst case counts).

Used by 1.4.3 (AA text), 1.4.6 (AAA text), and 1.4.11 (non-text: control borders, icons).
"""

from __future__ import annotations

import io
import re
from collections import Counter

from PIL import Image

from agent_access.jsassets import js
from agent_access.probes import probe

MAX_TEXT_ELEMENTS = 500
MAX_SAMPLED = 25

_RGB = re.compile(r"rgba?\(([^)]+)\)")


def parse_color(css: str | None) -> tuple[float, float, float, float] | None:
    if not css:
        return None
    m = _RGB.search(css)
    if not m:
        return None
    parts = [p for p in re.split(r"[ ,/]+", m.group(1)) if p]
    r, g, b = (float(x) for x in parts[:3])
    a = float(parts[3]) if len(parts) > 3 else 1.0
    return r, g, b, a


def relative_luminance(rgb: tuple[float, float, float]) -> float:
    def channel(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return round((hi + 0.05) / (lo + 0.05), 2)


def blend(fg: tuple[float, float, float, float], bg: tuple[float, float, float]) -> tuple[float, float, float]:
    """Composite a semi-transparent foreground over an opaque background."""
    a = fg[3]
    return tuple(fg[i] * a + bg[i] * (1 - a) for i in range(3))  # type: ignore[return-value]


def is_large_text(font_px: float, weight: int) -> bool:
    # WCAG: 18pt (24px) regular, or 14pt (18.66px) bold.
    return font_px >= 24 or (font_px >= 18.66 and weight >= 700)


def text_requirement(large: bool, level: str) -> float:
    if level == "AAA":
        return 4.5 if large else 7.0
    return 3.0 if large else 4.5


TEXT_JS = js("contrast/text")
NON_TEXT_JS = js("contrast/non_text")


@probe("contrast")
async def contrast(audit) -> dict:
    """{"text": [... ratio per text element], "non_text": [... control boundaries and icons]}"""
    return {"text": await measure_text(audit), "non_text": await measure_non_text(audit)}


def _sample_backgrounds(png: bytes, fg: tuple[float, float, float]) -> list[tuple[int, int, int]]:
    """CCA-style eyedropper: dominant colours in the element's pixels, excluding the text colour itself."""
    img = Image.open(io.BytesIO(png)).convert("RGB")
    img.thumbnail((120, 120))
    pixels = (img.getpixel((x, y)) for y in range(img.height) for x in range(img.width))
    counts = Counter((r // 8 * 8, g // 8 * 8, b // 8 * 8) for r, g, b in pixels)
    total = sum(counts.values())
    near_fg = lambda c: sum(abs(c[i] - fg[i]) for i in range(3)) < 60  # noqa: E731
    return [c for c, n in counts.most_common(8) if n / total >= 0.08 and not near_fg(c)][:3]


async def measure_text(audit) -> list[dict]:
    """Every visible text element with its contrast ratio. Background is computed, or sampled from pixels."""
    page = await audit.page()
    items = await page.evaluate(TEXT_JS, MAX_TEXT_ELEMENTS)
    results, sampled = [], 0
    for it in items:
        fg = parse_color(it["color"])
        if fg is None:
            continue
        bg_css, method = it["background"], "computed"
        if bg_css is None:
            if sampled >= MAX_SAMPLED or it["rect"]["width"] < 2 or it["rect"]["height"] < 2:
                continue
            sampled += 1
            try:
                png = await page.screenshot(clip=it["rect"], type="png", full_page=True)
            except Exception:
                continue
            candidates = _sample_backgrounds(png, fg[:3])
            if not candidates:
                continue
            ratios = [contrast_ratio(blend(fg, c), c) for c in candidates]
            ratio, method = min(ratios), "pixel-sampled"
            bg_css = "rgb(%d, %d, %d)" % candidates[ratios.index(ratio)]
        else:
            bg = parse_color(bg_css)[:3]
            ratio = contrast_ratio(blend(fg, bg), bg)
        large = is_large_text(it["font_px"], it["weight"])
        results.append({"selector": it["selector"], "text": it["text"], "foreground": it["color"], "background": bg_css,
                        "ratio": ratio, "large_text": large, "font_px": it["font_px"], "method": method})
    return results


async def measure_non_text(audit) -> list[dict]:
    page = await audit.page()
    results = []
    for it in await page.evaluate(NON_TEXT_JS, None):
        around = parse_color(it["around"]) if it["around"] else None
        if around is None:
            results.append({**it, "ratio": None, "method": "needs visual check (image/gradient behind)"})
            continue
        if it["kind"] == "control boundary":
            border = parse_color(it["border"]) if it["border"] else None
            fill = parse_color(it["fill"])
            # Either a contrasting border or a contrasting fill identifies the control.
            ratios = [contrast_ratio(blend(c, around[:3]), around[:3]) for c in (border, fill) if c and c[3] > 0]
            ratio = max(ratios) if ratios else 1.0
        else:
            color = parse_color(it["color"])
            ratio = contrast_ratio(blend(color, around[:3]), around[:3]) if color else None
        results.append({**it, "ratio": ratio, "method": "computed"})
    return results
