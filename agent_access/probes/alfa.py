"""Siteimprove Alfa (vendored at agent_access/vendor/alfa.min.js, MIT), a second rule engine run in the page like axe.

Its failures reach the rules only as questions (rules.Ctx.alfa): its precision on these pages isn't established, so it
never fails a criterion alone, and a failure of the probe leaves every rule's own result as it was.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from agent_access.context import AuditContext
from agent_access.jsassets import js
from agent_access.probes import probe

ALFA_PATH = Path(__file__).resolve().parent.parent / "vendor" / "alfa.min.js"
MAX_SELECTORS = 5
# Alfa took 3 s on 1,259 elements, 7 s on 2,509 and 22 s on 5,009 (axe: 5 s on 7,500), and the page is busy meanwhile.
MAX_ELEMENTS = 3000


@lru_cache(maxsize=1)
def alfa_source() -> str:
    return ALFA_PATH.read_text(encoding="utf-8")


@probe("alfa")
async def alfa(audit: AuditContext) -> dict:
    """{"version", "rules": [{id, message, criteria, elements, selectors}]}: Alfa's failed rules on the page."""
    page = await audit.page()
    if (n := await page.evaluate("() => document.getElementsByTagName('*').length")) > MAX_ELEMENTS:
        return {"error": f"not run: {n} elements, over the {MAX_ELEMENTS} the second engine runs on (it is slow on large pages)"}
    # As for axe: the bundle's own listeners aren't the page's.
    await page.evaluate("() => { if (window.__a11yInstr) window.__a11yInstr.paused = true; }")
    try:
        if not await page.evaluate("() => typeof window.__alfa !== 'undefined'"):
            await page.evaluate(alfa_source())
        outcomes = await page.evaluate(js("alfa/run"))
        version = await page.evaluate("() => window.__alfa.version")
    finally:
        await page.evaluate("() => { if (window.__a11yInstr) window.__a11yInstr.paused = false; }")
    rules: dict[str, dict] = {}
    for o in outcomes:
        # catalogue.get is None for removed criteria such as 4.1.1 Parsing.
        if criteria := [c for c in o["criteria"] if audit.catalogue.get(c)]:
            r = rules.setdefault(o["rule"], {"id": o["rule"], "message": " ".join(o["message"].split())[:200] or o["rule"],
                                             "criteria": criteria, "elements": 0, "selectors": []})
            r["elements"] += 1
            if o["selector"] and len(r["selectors"]) < MAX_SELECTORS:
                r["selectors"].append(o["selector"])
    return {"version": version, "rules": list(rules.values())}
