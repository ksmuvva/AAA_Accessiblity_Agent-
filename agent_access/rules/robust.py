"""Principle 4 - Robust: 4.1.2 and 4.1.3 (4.1.1 Parsing was removed in WCAG 2.2)."""

from __future__ import annotations

from agent_access.probes.screen_reader import spoken_name, spoken_role
from agent_access.rules import AXE, PW, SR, Ctx, wcag_tool
from agent_access.rules.common import completed_submissions, disclosure_groups, names, submission_facts, triggers


@wcag_tool("4.1.2", "check_name_role_value", domain="lead", tech=(AXE, PW, SR), uses=("axe", "disclosures", "screen_reader"))
def check_name_role_value(ctx: Ctx) -> None:
    """Every component needs an accessible name and role, and its state must be exposed and kept up to date."""
    ctx.axe()
    g = disclosure_groups(ctx.data("disclosures").get("results", []))
    if g["not_updated"]:
        ctx.fail(f"{len(g['not_updated'])} toggle(s) don't update aria-expanded when they open.",
                 triggers(g["not_updated"]), names(g["not_updated"]))
    if g["no_state"]:
        ctx.review(f"{len(g['no_state'])} control(s) show/hide content but expose no expanded state; inspect_element each "
                   "for role and aria-expanded: a custom toggle without them fails, details/summary and page links are fine.",
                   triggers(g["no_state"]), names(g["no_state"]))
    if g["opened"] and not (g["not_updated"] or g["no_state"]):
        ctx.passed("Toggles expose their expanded/collapsed state.")
    if g["unnamed"]:
        ctx.fail(f"{len(g['unnamed'])} dialog(s) have no accessible name.", triggers(g["unnamed"]), names(g["unnamed"]))

    sr = ctx.data("screen_reader")
    no_role = [f for f in sr.get("focus", []) if not spoken_role(f["spoken"] or "")]
    no_name = [f for f in sr.get("focus", []) if spoken_role(f["spoken"] or "") and not spoken_name(f["spoken"] or "")]
    who = "NVDA" if sr.get("backend") == "nvda" else "The screen reader (emulated)"

    def said(items):
        return "; ".join(f"{f['selector']}: '{f['spoken']}'" for f in items[:5])

    if no_role:
        ctx.fail(f"{who} announces {len(no_role)} focusable element(s) without a control role.",
                 [f["selector"] for f in no_role], said(no_role))
    if no_name:
        ctx.fail(f"{who} announces {len(no_name)} control(s) without a name.", [f["selector"] for f in no_name], said(no_name))
    ctx.detail("announcements", [f["spoken"] for f in sr.get("focus", [])])
    ctx.detail("screen_reader_backend", sr.get("backend"))


@wcag_tool("4.1.3", "check_status_messages", domain="forms", tech=(PW,), uses=("forms", "page"), judgement=True)
def check_status_messages(ctx: Ctx) -> None:
    """Status messages (results, errors, progress) must be announced without moving focus (live regions/roles)."""
    for s in completed_submissions(ctx.data("forms")):
        fx = submission_facts(s)
        if not fx["identified"]:
            continue
        if not fx["shown"]:
            # Browser validation bubbles move focus to the invalid field, where the message is announced.
            ctx.passed("Errors are reported by the browser's own validation messages, which are announced on the field.",
                       [n["selector"] for n in fx["native"]])
            continue
        # Error text that appears while focus stays where it was (on the submit button) must be in a live region;
        # if focus moved to the field or message, the screen reader announces it on focus.
        if not fx["alerts"] and not fx["focus_moved"] and not fx["described"]:
            ctx.fail("Error messages appeared but are not in a live region and focus didn't move to them.",
                     [n["selector"] for n in fx["new_text"]], "; ".join(fx["shown"][:3]))
        else:
            ctx.passed("Error messages are announced (live region, or focus moved to the error or field).")
    if ctx.findings:
        return
    regions = ctx.data("page").get("live_regions", [])
    note = "Measured by watching the DOM: NVDA live-region speech can't be captured through Guidepup."
    if regions:
        ctx.review(f"{len(regions)} live region(s) exist; check status messages after searches, cart updates, and saves use them.",
                   [r["selector"] for r in regions], note)
    else:
        ctx.review("No live regions found; check whether the page shows status messages (results counts, 'saved', progress) "
                   "and that they're announced.", evidence=note)
