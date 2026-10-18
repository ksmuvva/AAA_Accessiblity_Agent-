"""Read-only page probes: content inventory, instrumentation log, navigation, media, and animation."""

from __future__ import annotations

from agent_access.context import AuditContext
from agent_access.jsassets import js
from agent_access.probes import probe


async def _eval(audit: AuditContext, script: str, arg=None):
    return await (await audit.page()).evaluate(js(script), arg)


@probe("page")
async def page(audit: AuditContext) -> dict:
    """Text, links, images, headings, landmarks, paragraphs, passages with their lang, sign-in fields, CAPTCHA,
    skip link, carousels, sliders, draggables, live regions, open dialogs, meta refresh, CSS reordering."""
    return await _eval(audit, "page/inventory")


@probe("instrumentation")
async def instrumentation(audit: AuditContext) -> dict:
    """Event listeners by type, long timers, and window.open calls, logged by the init script since page load."""
    return await _eval(audit, "page/instrumentation")


def primary_nav(snapshot: dict) -> list[str]:
    navs = snapshot["nav_landmarks"]
    if navs:
        return [link["text"] for link in max(navs, key=lambda n: len(n["links"]))["links"]]
    # No nav landmark: header links, else the block that looks like navigation (nav.js nav_group).
    return [link["text"] for link in snapshot["header_links"] or snapshot.get("nav_group", [])]


@probe("nav")
async def nav(audit: AuditContext) -> dict:
    """Nav landmarks and links, header/footer links, search, site map, breadcrumb, help links (in order).

    The snapshot is also kept in audit.nav_snapshots so rules can compare pages (3.2.3, 3.2.4, 3.2.6).
    """
    snap = await _eval(audit, "page/nav")
    audit.nav_snapshots[audit.current_url] = snap
    return {**snap, "primary_nav": primary_nav(snap)[:30]}


@probe("media")
async def media(audit: AuditContext) -> dict:
    """Video/audio elements (tracks, with each caption track's cues and gaps; autoplay, controls), player iframes,
    objects, canvas, animations, GIFs."""
    return await _eval(audit, "page/media")


@probe("motion")
async def motion(audit: AuditContext) -> dict:
    """Running animations with and without prefers-reduced-motion, and reduced-motion CSS rules."""
    p = await audit.page()
    normal = await p.evaluate(js("page/animations"))
    await p.emulate_media(reduced_motion="reduce")
    await p.wait_for_timeout(300)
    reduced = await p.evaluate(js("page/animations"))
    await p.emulate_media(reduced_motion="no-preference")
    return {"running_animations": normal["running"], "running_with_reduced_motion": reduced["running"],
            "reduced_motion_rules": normal["reduced_motion_rules"]}
