"""Forms probe: inventory, submissions with empty/invalid values, and control changes.

Every submission runs inside AuditContext.network_guard, so nothing reaches the server and the page can't
navigate away; the page is reloaded afterwards. Server-side validation therefore can't be observed: a form that
*tried* to submit is reported as such and the rules treat it as needs_review.
"""

from __future__ import annotations

from agent_access.context import AuditContext
from agent_access.intent import destroys
from agent_access.jsassets import js
from agent_access.probes import probe
from agent_access.probes.interaction import newly_visible, visible_ids

MAX_FORMS = 4
MAX_CHANGE_CONTROLS = 8
SUBMIT_WAIT_MS = 1000
NO_SUBMIT = "no way to submit found"
# Fields that usually collect information about the user, so 1.3.5 needs an autocomplete token.
PERSONAL_FIELD_PATTERN = (
    "name|fname|lname|surname|given|family|email|e-mail|phone|tel|mobile|address|street|city|town|zip|postal|"
    "postcode|country|region|state|province|birth|bday|dob|username|organi[sz]ation|company|cc-|card"
)
FILL_INVALID = {"email": "not-an-email", "tel": "abc", "url": "not a url", "text": "x", "search": "x",
                "password": "x", "textarea": "x"}
# What a form says it does: its action URL and its buttons' words.
FORM_INTENT_JS = """(f) => [f.getAttribute('action') || '', ...[...f.querySelectorAll('button, input[type=submit], input[type=image]')]
  .map(b => b.innerText || b.value || b.getAttribute('aria-label') || '')].join(' | ')"""
TEXT_FIELDS = "input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=checkbox]):not([type=radio]), textarea"


async def list_forms(audit: AuditContext) -> list[dict]:
    return await (await audit.page()).evaluate(js("forms/list_forms"), PERSONAL_FIELD_PATTERN)


async def _fill(page, root, mode: str) -> list[str]:
    """Clear text fields (empty mode) or fill them with wrongly formatted values (invalid mode)."""
    changed = []
    fields = root.locator(TEXT_FIELDS)
    for i in range(min(await fields.count(), 25)):
        try:
            kind = await fields.nth(i).evaluate("(el) => el.tagName === 'TEXTAREA' ? 'textarea' : el.type")
            if kind in FILL_INVALID:
                await fields.nth(i).fill("" if mode == "empty" else FILL_INVALID[kind], timeout=2000)
                changed.append(kind)
        except Exception:
            continue
    return changed


async def _submit(root, is_form: bool) -> str:
    """Submit: click the form's first visible submit control, else press Enter in its first field.

    Controls outside any <form> have no submit control of their own (any button on the page could be anything),
    so they only get Enter. A control that can't be clicked in time is skipped, not a probe failure.
    """
    if is_form:
        submit = root.locator('button[type="submit"], input[type="submit"], input[type="image"], button:not([type])')
        for i in range(min(await submit.count(), 5)):
            candidate = submit.nth(i)
            if await candidate.is_visible():
                try:
                    await candidate.click(timeout=3000)
                    return "clicked submit button"
                except Exception:
                    break
    field = root.locator(TEXT_FIELDS).first
    if await field.count():
        try:
            await field.press("Enter", timeout=3000)
            return "pressed Enter in the first field"
        except Exception:
            return NO_SUBMIT
    return NO_SUBMIT


async def submit_test(audit: AuditContext, form: str | None, mode: str) -> dict:
    """Submit one form (None = controls outside any form) and record every visible sign of an error."""
    page = await audit.page()
    if form and not await page.locator(form).count():
        return {"form": form, "mode": mode, "error": f"No element matches {form!r}."}
    root = page.locator(form) if form else page.locator("body")
    is_form = bool(form) and form != "body" and await root.evaluate("(el) => el.tagName === 'FORM'")
    if is_form and (danger := destroys(await root.evaluate(FORM_INTENT_JS))):
        return {"form": form, "mode": mode, "skipped": f"Not submitted: it looks destructive ({danger})."}
    before = await visible_ids(page)
    has_required = await page.evaluate(js("forms/has_required"), form)
    state, new_text = None, []
    async with audit.network_guard() as blocked:
        filled = await _fill(page, root, mode)
        how = await _submit(root, is_form)
        if how != NO_SUBMIT:
            await page.wait_for_timeout(SUBMIT_WAIT_MS)
            state = await page.evaluate(js("forms/error_state"), form)
            new_text = [{"selector": n["selector"], "text": n["text"]} for n in await newly_visible(page, before) if n["text"]]
    if audit.can_restore():
        await audit.restore_page()
    out = {"form": form, "mode": mode, "submit": how, "fields_changed": filled, "has_required": has_required,
           "tried_to_submit": bool(blocked), "blocked_requests": blocked[:5]}
    if how == NO_SUBMIT:
        return {**out, "skipped": "No visible submit control or text field could be used to submit it."}
    if state is None:
        return {**out, "error": "The form disappeared after submitting."}
    return {**out, "state": state, "new_text": new_text}


async def change_test(audit: AuditContext, form: str | None) -> dict:
    """Change each select/checkbox/radio without submitting and record any navigation or requests (3.2.2)."""
    page = await audit.page()
    controls = (page.locator(form) if form else page.locator("body")).locator("select, input[type=checkbox], input[type=radio]")
    changes = []
    async with audit.network_guard() as blocked:
        for i in range(min(await controls.count(), MAX_CHANGE_CONTROLS)):
            control = controls.nth(i)
            try:
                selector = await control.evaluate(js("forms/css_path"))
                url_before, seen = page.url, len(blocked)
                if await control.evaluate("(el) => el.tagName") == "SELECT":
                    if await control.locator("option").count() < 2:
                        continue
                    current = await control.evaluate("(el) => el.selectedIndex")
                    await control.select_option(index=1 if current != 1 else 0, timeout=2000)
                else:
                    await control.check(timeout=2000)
                await page.wait_for_timeout(500)
                new = blocked[seen:]
                changes.append({"control": selector, "navigation": [b for b in new if b.endswith("(navigation)")][:1],
                                "requests": [b for b in new if not b.endswith("(navigation)")][:2],
                                "url_changed": page.url != url_before})
            except Exception:
                continue
    if audit.can_restore():
        await audit.restore_page()
    return {"form": form, "changes": changes}


def _has_changeable(form: dict) -> bool:
    return any(f["tag"] == "select" or f["type"] in ("checkbox", "radio") for f in form["fields"])


@probe("forms", retestable=True)
async def forms(audit: AuditContext, targets: list[str] | None = None) -> dict:
    """{"forms": inventory, "submissions": [submit_test...], "changes": [change_test...]}

    With targets (form selectors), only those are tested; the inventory still covers the whole page.
    """
    inventory = await list_forms(audit)
    out = {"forms": inventory, "submissions": [], "changes": []}
    if not audit.can_restore():
        return {**out, "note": "Form tests skipped in a process state (they would disturb the flow)."}
    if targets:
        known = {f["form_selector"]: f for f in inventory}
        # A target the inventory doesn't know (e.g. a form-like div) gets both kinds of test.
        plan = [(t, _has_changeable(known[t]) if t in known else True) for t in targets]
    else:
        plan = [(f["form_selector"], _has_changeable(f)) for f in inventory[:MAX_FORMS] if f["fields"]]
    for form, changeable in plan:
        # One form's failure (a control that can't be clicked, a page that navigates) is recorded on that entry;
        # the other forms and the inventory still come back, so the rules get a verdict instead of an error.
        for mode in ("empty", "invalid"):
            try:
                out["submissions"].append(await submit_test(audit, form, mode))
            except Exception as exc:
                out["submissions"].append({"form": form, "mode": mode, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
                if audit.can_restore():
                    await audit.restore_page()
        if changeable:
            try:
                out["changes"].append(await change_test(audit, form))
            except Exception as exc:
                out["changes"].append({"form": form, "changes": [], "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
                if audit.can_restore():
                    await audit.restore_page()
    return out
