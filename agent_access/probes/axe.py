"""axe-core (vendored at agent_access/vendor/axe.min.js, MPL-2.0): every A, AA, and AAA rule, mapped to WCAG criteria."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from agent_access.context import AuditContext
from agent_access.jsassets import js
from agent_access.probes import probe

AXE_PATH = Path(__file__).resolve().parent.parent / "vendor" / "axe.min.js"
ALL_TAGS = ["wcag2a", "wcag21a", "wcag2aa", "wcag21aa", "wcag22aa", "wcag2aaa"]
# Success-criterion tags look like "wcag111" (1.1.1) or "wcag1412" (1.4.12).
SC_TAG = re.compile(r"^wcag(\d)(\d)(\d+)$")
OUTCOMES = {"violations": "fail", "incomplete": "needs_review", "passes": "pass"}
MAX_SELECTORS = 5
MAX_FRAMES = 10
ENTER_FRAME = " >> internal:control=enter-frame >> "   # Playwright's step into an iframe


@lru_cache(maxsize=1)
def axe_source() -> str:
    return AXE_PATH.read_text(encoding="utf-8")


def criteria_from_tags(tags: list[str]) -> list[str]:
    return [".".join(m.groups()) for m in map(SC_TAG.match, tags) if m]


def _selectors(nodes: list[dict]) -> list[str]:
    # node["target"] holds one entry per frame level (outermost first); an entry inside shadow DOM is itself a list,
    # host first. Joined as Playwright selectors, so inspect_element and the other tools can find the element.
    return [ENTER_FRAME.join(" >> ".join(t) if isinstance(t, list) else str(t) for t in n.get("target", []))
            for n in nodes[:MAX_SELECTORS]]


@probe("axe")
async def axe(audit: AuditContext) -> dict:
    """{"version", "rules": [{id, help, outcome, criteria, elements, selectors, evidence}]}"""
    page = await audit.page()
    # axe-core registers its own window listeners; pause the instrumentation wrapper so they aren't counted as the page's.
    await page.evaluate("() => { if (window.__a11yInstr) window.__a11yInstr.paused = true; }")
    try:
        # axe in the page and each frame, talking across origins, so axe.run reports inside frames too.
        for i, frame in enumerate(page.frames[:MAX_FRAMES + 1]):
            try:
                if not await frame.evaluate("() => typeof window.axe !== 'undefined'"):
                    await frame.add_script_tag(content=axe_source())
                await frame.evaluate("() => axe.configure({allowedOrigins: ['<unsafe_all_origins>']})")
            except Exception:
                if i == 0:
                    raise
                # a detached or sandboxed frame: axe skips it
        raw = await page.evaluate(js("axe/run"), ALL_TAGS)
    finally:
        await page.evaluate("() => { if (window.__a11yInstr) window.__a11yInstr.paused = false; }")
    rules = []
    for key, outcome in OUTCOMES.items():
        for r in raw[key]:
            # catalogue.get is None for removed criteria such as 4.1.1 Parsing.
            criteria = [c for c in criteria_from_tags(r.get("tags", [])) if audit.catalogue.get(c)]
            if not criteria:
                continue
            nodes = r.get("nodes", [])
            evidence = (nodes[0].get("failureSummary") or nodes[0].get("html") or "")[:300] if nodes and outcome != "pass" else None
            rules.append({"id": r["id"], "help": r.get("help", r["id"]), "outcome": outcome,
                          "criteria": criteria, "elements": len(nodes),
                          "selectors": _selectors(nodes) if outcome != "pass" else [], "evidence": evidence or None})
    return {"version": raw["version"], "rules": rules}
