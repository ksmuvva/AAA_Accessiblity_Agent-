"""Interactive probes: hover/focus pop-ups, menus and dialogs, single-key shortcuts, pointer down-events,
sliders, the skip link, and inactivity on a faked clock.

Every probe here runs inside AuditContext.network_guard (no server writes, no navigation away) and restores
the page afterwards, except `timeouts`, which runs in a browser context of its own. Triggers whose names suggest
a destructive action (delete, buy, submit...) are skipped.
`hover` and `disclosures` accept targets=[selectors] so agents can re-test elements auto-detection missed.
"""

from __future__ import annotations

import asyncio

from agent_access.context import SAFE_METHODS, AuditContext, base_page_key
from agent_access.intent import DESCRIBE_JS, submits
from agent_access.jsassets import js
from agent_access.probes import collect, probe

MAX_HOVER_TRIGGERS = 15
MAX_DISCLOSURE_TRIGGERS = 12
MAX_POINTER_TARGETS = 6
SHORTCUT_KEYS = ["s", "/", "?", "j", "k", "n", "p", "f", "m", "g", "c", "1"]
PAGE_STATE_JS = ("() => ({ scroll: scrollY, hash: location.hash, "
                 "focus: document.activeElement && document.activeElement !== document.body ? document.activeElement.tagName : null })")
BLUR_JS = "() => { document.activeElement && document.activeElement.blur(); }"
SETTLE_JS = js("page/settle")


async def settle(page, cap_ms: int = 1500) -> int:
    """Wait until the page settles after an action (quiet DOM, no transition, no short timer), at most `cap_ms`."""
    return await page.evaluate(SETTLE_JS, [150, cap_ms])


# ---------------------------------------------------------------- shared change detection

async def visible_ids(page) -> list:
    return await page.evaluate(js("interaction/visible_ids"), None)


async def newly_visible(page, before_ids: list) -> list[dict]:
    """The outermost elements visible now that weren't before (with text, position, and whether they cover text)."""
    return await page.evaluate(js("interaction/new_visible"), before_ids)


async def _snapshot(page) -> tuple[dict, list]:
    return await page.evaluate(PAGE_STATE_JS), await visible_ids(page)


async def _changes(page, before: tuple[dict, list], blocked: list[str], seen_blocked: int) -> list[str]:
    """What changed since `before`: focus, URL fragment, scroll, new content, or an attempted request."""
    state, ids = before
    now = await page.evaluate(PAGE_STATE_JS)
    changes = []
    if now["focus"] and now["focus"] != state["focus"]:
        changes.append(f"focus moved to a {now['focus'].lower()}")
    if now["hash"] != state["hash"]:
        changes.append(f"URL fragment changed to {now['hash']}")
    if abs(now["scroll"] - state["scroll"]) > 40:
        changes.append("page scrolled")
    if revealed := await newly_visible(page, ids):
        changes.append(f"new content appeared: '{revealed[0]['text'][:40]}'")
    if len(blocked) > seen_blocked:
        changes.append(f"tried to load {blocked[-1]}")
    return changes


async def _reset(audit: AuditContext, page) -> str | None:
    """Undo an interaction test. Call it outside network_guard: restoring a URL page reloads it, and the guard
    blocks main-frame navigation. Returns why the page couldn't be restored, or None."""
    try:
        if audit.can_restore():
            await audit.restore_page()
        else:
            await page.keyboard.press("Escape")
    except Exception as exc:
        return f"{type(exc).__name__}: {str(exc)[:120]}"
    return None


# ---------------------------------------------------------------- hover / focus pop-ups (1.4.13)

async def _still_visible(page, element_id) -> bool:
    return await page.evaluate(js("interaction/still_visible"), element_id)


async def _hover_test(page, selector: str) -> dict | None:
    trigger = page.locator(selector).first
    await page.mouse.move(1, 1)
    await page.wait_for_timeout(150)
    before = await visible_ids(page)
    try:
        await trigger.hover(timeout=3000)
    except Exception:
        return None
    await settle(page)
    revealed = await newly_visible(page, before)
    if not revealed:
        return None
    content = revealed[0]
    await page.mouse.move(content["x"], content["y"], steps=8)  # hoverable: pointer moves onto the pop-up
    await page.wait_for_timeout(300)
    hoverable = await _still_visible(page, content["id"])
    await page.wait_for_timeout(1000)  # persistent: it stays while the pointer stays
    persistent = await _still_visible(page, content["id"]) if hoverable else None
    await trigger.hover(timeout=3000)  # dismissible: Escape hides it without moving the pointer
    await page.wait_for_timeout(300)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(300)
    dismissible = not await _still_visible(page, content["id"])
    await page.mouse.move(1, 1)
    return {"trigger": selector, "content": content["text"], "content_selector": content["selector"],
            "hoverable": hoverable, "persistent": persistent, "dismissible": dismissible, "obscures": content["obscures"]}


async def _focus_test(page, selector: str) -> dict | None:
    trigger = page.locator(selector).first
    await page.evaluate(BLUR_JS)
    before = await visible_ids(page)
    try:
        await trigger.focus(timeout=2000)
    except Exception:
        return None
    await settle(page)
    revealed = await newly_visible(page, before)
    if not revealed:
        return None
    content = revealed[0]
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(300)
    dismissible = not await _still_visible(page, content["id"])
    await page.evaluate(BLUR_JS)
    return {"trigger": selector, "content": content["text"], "content_selector": content["selector"],
            "dismissible": dismissible, "obscures": content["obscures"]}


@probe("hover", retestable=True)
async def hover(audit: AuditContext, targets: list[str] | None = None) -> dict:
    """For each trigger: does hover/focus reveal content, and is that content hoverable, persistent, dismissible?"""
    page = await audit.page()
    triggers = list(targets or []) or await page.evaluate(js("interaction/hover_candidates"), MAX_HOVER_TRIGGERS)
    hover_results, focus_results = [], []
    async with audit.network_guard():
        for selector in triggers[:MAX_HOVER_TRIGGERS]:
            try:
                if (h := await _hover_test(page, selector)) is not None:
                    hover_results.append(h)
                if (f := await _focus_test(page, selector)) is not None:
                    focus_results.append(f)
            except Exception:
                continue  # A detached or invalid trigger must not stop the others.
    if audit.can_restore():
        await audit.restore_page()
    return {"triggers_tested": len(triggers[:MAX_HOVER_TRIGGERS]), "hover": hover_results, "focus": focus_results}


# ---------------------------------------------------------------- menus, disclosures, dialogs

async def _trigger_state(page, selector: str) -> dict | None:
    return await page.evaluate(js("interaction/trigger_state"), selector)


async def _opened(page, selector: str, before_ids: list, before_state: dict) -> tuple[bool, list]:
    revealed = await newly_visible(page, before_ids)
    state = await _trigger_state(page, selector)
    changed = state is not None and (state["expanded"] != before_state["expanded"] or state["open_attr"] != before_state["open_attr"])
    return bool(revealed) or changed, revealed


async def _test_trigger(page, selector: str, name: str) -> dict:
    result: dict = {"trigger": selector, "name": name}
    trigger = page.locator(selector).first
    before_ids = await visible_ids(page)
    before_state = await _trigger_state(page, selector)
    if before_state is None:
        return {**result, "error": "trigger not found"}
    opened_by, revealed = None, []
    for key in ("Enter", "Space"):
        await trigger.focus(timeout=2000)
        await page.keyboard.press(key)
        await settle(page)
        opened, revealed = await _opened(page, selector, before_ids, before_state)
        if opened:
            opened_by = key
            break
    if not opened_by:
        await trigger.click(timeout=3000)
        await settle(page)
        opened, revealed = await _opened(page, selector, before_ids, before_state)
        opened_by = "mouse only" if opened else None
    result["opened_by"] = opened_by
    if not opened_by:
        return result
    after_state = await _trigger_state(page, selector)
    result["aria_expanded"] = {"before": before_state["expanded"], "after": after_state["expanded"] if after_state else None}
    result["content"] = revealed[0]["text"] if revealed else None
    dialog = await page.evaluate(js("interaction/dialog_state"), [r["id"] for r in revealed])
    result.update(is_dialog=dialog["is_dialog"], dialog_name=dialog["dialog_name"], focus_moved_inside=dialog["focus_inside"])
    if dialog["is_dialog"] and dialog["dialog_selector"]:
        if dialog["modal"] and dialog["focusable_count"]:  # focus containment: Tab past the end stays inside
            for _ in range(dialog["focusable_count"] + 2):
                await page.keyboard.press("Tab")
            result["focus_contained"] = await page.evaluate(js("interaction/focus_inside"), dialog["dialog_selector"])
        result["close_button"] = dialog["close_button"]
    await page.keyboard.press("Escape")  # Escape should close it; focus should then return to the trigger
    await settle(page)
    still_open, _ = await _opened(page, selector, before_ids, before_state)
    result["closes_with_escape"] = not still_open
    if not still_open:
        after_close = await _trigger_state(page, selector)
        result["focus_returned"] = bool(after_close and after_close["focused"])
    return result


async def _label(page, selector: str) -> str:
    """What an agent-given target says (an unreadable one is judged by its selector alone)."""
    try:
        return await page.locator(selector).first.evaluate(DESCRIBE_JS, timeout=2000)
    except Exception:
        return selector


@probe("disclosures", retestable=True)
async def disclosures(audit: AuditContext, targets: list[str] | None = None) -> dict:
    """Open each menu/accordion/dialog from the keyboard (then mouse) and record how it behaves."""
    page = await audit.page()
    triggers = ([{"selector": s, "name": await _label(page, s)} for s in targets] if targets
                else await page.evaluate(js("interaction/disclosure_candidates"), MAX_DISCLOSURE_TRIGGERS))
    results, skipped, unrestored = [], [], None
    for t in triggers[:MAX_DISCLOSURE_TRIGGERS]:
        if submits(t["name"]):
            skipped.append(t["name"])
            continue
        async with audit.network_guard():
            try:
                results.append(await _test_trigger(page, t["selector"], t["name"]))
            except Exception as exc:
                results.append({**t, "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
        # Each trigger starts from a clean page; if the page can't be restored, later results would be unreliable.
        if unrestored := await _reset(audit, page):
            break
    return {"results": results, "skipped_risky": skipped, **({"stopped_early": unrestored} if unrestored else {})}


# ---------------------------------------------------------------- keys, pointer, sliders, skip link

@probe("shortcuts")
async def shortcuts(audit: AuditContext) -> dict:
    """Press single printable keys with focus on the page body (2.1.4) and record what each one does."""
    page = await audit.page()
    triggered, unrestored = [], None
    for key in SHORTCUT_KEYS:
        async with audit.network_guard() as blocked:
            await page.evaluate(BLUR_JS)
            before = await _snapshot(page)
            await page.keyboard.press(key)
            await settle(page, 1000)
            effects = await _changes(page, before, blocked, 0)
        if effects:
            triggered.append({"key": key, "effects": effects})
            if unrestored := await _reset(audit, page):
                break
    return {"keys_tested": SHORTCUT_KEYS, "triggered": triggered, **({"stopped_early": unrestored} if unrestored else {})}


@probe("pointer")
async def pointer(audit: AuditContext) -> dict:
    """For elements with down-event handlers: does pressing (without releasing) already act? (2.5.2)"""
    page = await audit.page()
    listeners = (await collect(audit, "instrumentation")).data.get("listeners", {})
    candidates = dict.fromkeys(listeners.get("mousedown", []) + listeners.get("pointerdown", []) + listeners.get("touchstart", []))
    results, unrestored = [], None
    for sel in [t for t in candidates if t not in ("window", "document", "other")][:MAX_POINTER_TARGETS]:
        loc = page.locator(sel).first
        try:
            if (await loc.evaluate("(el) => ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) || el.isContentEditable")
                    or submits(await loc.evaluate(DESCRIBE_JS))):
                continue
            box = await loc.bounding_box()
        except Exception:
            continue
        if not box:
            continue
        async with audit.network_guard() as blocked:
            before = await _snapshot(page)
            await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            await page.mouse.down()
            await page.wait_for_timeout(300)
            on_down = [c for c in await _changes(page, before, blocked, 0) if not c.startswith("focus moved")]
            await page.mouse.move(box["x"] + box["width"] + 200, box["y"] + box["height"] + 200, steps=4)  # abort
            await page.mouse.up()
        results.append({"selector": sel, "acts_on_down": on_down})
        if audit.can_restore() and (unrestored := await _reset(audit, page)):
            break
    return {"results": results, **({"stopped_early": unrestored} if unrestored else {})}


@probe("sliders")
async def sliders(audit: AuditContext) -> dict:
    """Does each slider respond to arrow keys (an alternative to dragging)?"""
    page = await audit.page()
    results = []
    for sel in (await collect(audit, "page")).data.get("sliders", []):
        loc = page.locator(sel).first
        try:
            await loc.focus(timeout=2000)
            before = await loc.evaluate(js("interaction/slider"))
            await page.keyboard.press("ArrowRight")
            await page.wait_for_timeout(150)
            after = await loc.evaluate(js("interaction/slider"))
            await page.keyboard.press("ArrowLeft")
            results.append({"selector": sel, "keyboard_changes_value": before != after})
        except Exception as exc:
            results.append({"selector": sel, "error": str(exc)[:80]})
    return {"sliders": results}


SKIP_TARGET_JS = """(id) => {
  const t = id && document.getElementById(id);
  if (!t) return { target_exists: false, focus_in_target: false };
  const a = document.activeElement, r = t.getBoundingClientRect();
  // Does the link land on the main content? (the main landmark, or with no landmark, an <h1> at or in the target)
  const main = document.querySelector('main, [role="main"]');
  const onMain = main ? t === main || t.contains(main) || main.contains(t) : t.matches('h1') || !!t.querySelector('h1');
  return { target_exists: true, focus_in_target: t === a || t.contains(a), target_in_view: r.top >= -5 && r.top < innerHeight,
           lands_on_main: onMain };
}"""


@probe("skip_link")
async def skip_link(audit: AuditContext) -> dict:
    """Activate the skip link (if any) and check that it moves to its target."""
    link = (await collect(audit, "page")).data.get("skip_link")
    if not link:
        return {"skip_link": None}
    page = await audit.page()
    async with audit.network_guard():
        await page.locator(link["selector"]).first.focus(timeout=2000)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(300)
        lands = await page.evaluate(SKIP_TARGET_JS, (link["target"] or "#")[1:])
    if audit.can_restore():
        await audit.restore_page()
    return {"skip_link": link, **lands}


IDLE_MINUTES = [*range(5, 61, 5), 20 * 60]  # every 5 minutes for an hour, then 20 hours (WCAG's 20-hour exception)


async def _cut(route) -> None:
    await (route.continue_() if route.request.method in SAFE_METHODS else route.abort())


@probe("timeouts")
async def timeouts(audit: AuditContext) -> dict:
    """The page left idle on a faked clock: each new time-limit warning (with any control to extend it), dialog and
    navigation, at the idle time it happened. A clock can't be uninstalled, so this loads the page again in a browser
    context of its own, with the run's cookies, where only GET requests go out and WebSockets reach nothing. A
    jump fires each due timer once, hence the 5-minute steps. Server-side session expiry can't be faked."""
    main = await audit.page()
    context = await main.context.browser.new_context(viewport=main.viewport_size, service_workers="block",
                                                     storage_state=await main.context.storage_state(indexed_db=True))
    try:
        await context.route("**/*", _cut)
        await context.route_web_socket("**/*", lambda ws: None)
        page = await context.new_page()
        dialogs, moves = [], []
        page.on("dialog", lambda d: (dialogs.append(d.message[:120]), asyncio.ensure_future(d.dismiss())))
        page.on("framenavigated", lambda f: moves.append(f.url) if f == page.main_frame else None)
        await page.clock.install()
        key = base_page_key(audit.current_url)
        await (page.set_content(audit.inline_html[key]) if key in audit.inline_html else page.goto(key, wait_until="load"))
        moves.clear()
        seen, events, idle = set((await page.evaluate(js("page/idle_text")))["warnings"]), [], 0
        for minutes in IDLE_MINUTES:
            await page.clock.fast_forward((minutes - idle) * 60_000)
            idle = minutes
            await page.wait_for_timeout(250)
            try:
                now = await page.evaluate(js("page/idle_text"))
            except Exception:  # the page is navigating away
                now = {"warnings": [], "controls": []}
            new = [w for w in now["warnings"] if w not in seen]
            seen.update(new)
            if new or dialogs or moves:
                events.append({"idle_minutes": minutes, "warnings": new[:3], "controls": now["controls"] if new else [],
                               "dialogs": dialogs[:3], "navigated_to": moves[-1] if moves else None})
                dialogs.clear()
            if moves:
                break
        return {"events": events}
    finally:
        await context.close()
