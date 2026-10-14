"""Facts derived from probe data that several rules need. These describe what happened; the rules judge."""

from __future__ import annotations

from agent_access.rules import Ctx


def listener_targets(listeners: dict, *types: str) -> list[str]:
    return list(dict.fromkeys(t for ty in types for t in listeners.get(ty, []) if t != "other"))


def inspectable_stops(keyboard: dict) -> list[dict]:
    """Tab stops other than iframes: focus inside a (possibly cross-origin) frame can't be inspected."""
    return [s for s in keyboard.get("stops", []) if s["tag"] != "iframe"]


def names(items: list[dict], key: str = "name") -> str:
    return "; ".join(f"'{i.get(key, '')}'" for i in items[:5])


# ---------------------------------------------------------------- media

def has_video(m: dict) -> bool:
    return bool(m.get("videos") or m.get("player_iframes"))


def has_media(m: dict) -> bool:
    return bool(has_video(m) or m.get("audio") or m.get("objects"))


def no_media_at_all(m: dict) -> bool:
    """Strict: nothing that could hold media, so 1.2.x and 1.4.2 are not applicable."""
    return not (has_media(m) or m.get("canvas") or m.get("other_iframes"))


def media_selectors(m: dict) -> list[str]:
    return [x["selector"] for x in m.get("videos", []) + m.get("audio", []) + m.get("player_iframes", [])]


# ---------------------------------------------------------------- menus, disclosures, dialogs

def disclosure_groups(results: list[dict]) -> dict[str, list[dict]]:
    opened = [r for r in results if r.get("opened_by") and "error" not in r]
    dialogs = [r for r in opened if r.get("is_dialog")]
    toggles = [r for r in opened if not r.get("is_dialog")]
    return {
        "opened": opened,
        "mouse_only": [r for r in opened if r["opened_by"] == "mouse only"],
        "not_updated": [r for r in toggles if r["aria_expanded"]["before"] is not None
                        and r["aria_expanded"]["after"] == r["aria_expanded"]["before"]],
        "no_state": [r for r in toggles if r["aria_expanded"]["before"] is None and not r["trigger"].endswith("summary")],
        "dialogs": dialogs,
        "focus_not_moved": [r for r in dialogs if not r["focus_moved_inside"]],
        "unnamed": [r for r in dialogs if not r["dialog_name"]],
        "escapes_modal": [r for r in dialogs if r.get("focus_contained") is False],
        "stuck": [r for r in dialogs if not r["closes_with_escape"] and not r.get("close_button")],
        "focus_lost": [r for r in dialogs if r["closes_with_escape"] and r.get("focus_returned") is False],
    }


def triggers(items: list[dict]) -> list[str]:
    return [r["trigger"] for r in items]


# ---------------------------------------------------------------- target size

def target_size(ctx: Ctx, minimum: int, r: dict) -> None:
    if r.get("failures"):
        ctx.fail(f"{len(r['failures'])} target(s) are smaller than {minimum}x{minimum}px"
                 + (" without enough spacing." if minimum == 24 else "."), [f["selector"] for f in r["failures"]],
                 "; ".join(f"'{f['name'][:20]}' {f['width']}x{f['height']}" for f in r["failures"][:6]))
    if r.get("ua_review"):
        ctx.review(f"{len(r['ua_review'])} small native checkbox/radio control(s); exempt only at browser-default size. "
                   "inspect_element each: a custom width/height or appearance: none means it must meet the size.",
                   [f["selector"] for f in r["ua_review"]])
    if not r.get("failures") and not r.get("ua_review") and r.get("total_targets"):
        ctx.passed(f"All {r['total_targets']} targets are at least {minimum}x{minimum}px"
                   + (" or adequately spaced." if minimum == 24 else "."))


# ---------------------------------------------------------------- form submissions

def submission_facts(s: dict) -> dict:
    """What a submission showed: messages, aria-invalid fields with/without text, browser validation, and whether
    focus moved to an error message or an invalid field (rather than staying on the control that submitted)."""
    state = s["state"]
    shown = list(dict.fromkeys([n["text"] for n in s["new_text"]] + state["alerts"]))
    described = [a for a in state["aria_invalid"] if a["described_by_text"]]
    error_targets = {x["selector"] for x in s["new_text"] + state["aria_invalid"] + state["native_invalid"]}
    return {"shown": shown, "described": described, "native": state["native_invalid"], "aria_invalid": state["aria_invalid"],
            "alerts": state["alerts"], "focus_in_form": state["focus_in_form"], "new_text": s["new_text"],
            "focus_moved": state.get("focus") in error_targets,
            "identified": bool(shown or described or state["native_invalid"])}


def completed_submissions(forms: dict) -> list[dict]:
    return [s for s in forms.get("submissions", []) if "state" in s]


def untested_submissions(forms: dict) -> list[tuple[str | None, str]]:
    """(form, reason) for every form whose automated submission didn't happen (skipped, errored, or not attempted)."""
    out = {}
    for s in forms.get("submissions", []):
        if "state" not in s:
            out.setdefault(s["form"], s.get("skipped") or s.get("error") or "not tested")
    if not forms.get("submissions") and forms.get("note"):
        for form in forms.get("forms", []):
            out.setdefault(form["form_selector"], forms["note"])
    return list(out.items())


def form_fields(forms: dict) -> list[dict]:
    return [f for form in forms.get("forms", []) for f in form["fields"]]


# ---------------------------------------------------------------- Tab walk coverage

def walk_cut_short(kb: dict) -> str:
    """Why the Tab walk may not have covered the page: it ended inside an embedded frame (often a consent dialog)."""
    f = kb.get("stuck_in_iframe")
    return (f"The Tab walk ended inside the frame {f['selector']} ('{f.get('name') or 'unnamed'}'), e.g. a cookie or "
            "consent dialog, so elements after it weren't reached; check again after dismissing it.") if f else ""


def pass_walk(ctx: Ctx, kb: dict, message: str, selectors=()) -> None:
    """A pass over the Tab stops, or a review when the walk didn't reach the whole page."""
    if cut := walk_cut_short(kb):
        ctx.review(f"Only the first {len(inspectable_stops(kb))} focus stop(s) could be checked; "
                   + message[0].lower() + message[1:], selectors, cut)
    else:
        ctx.passed(message, selectors)


def no_stops(ctx: Ctx, kb: dict) -> None:
    ctx.review("Tab reached no focus stops outside embedded frames, so this couldn't be measured; check whether a dialog "
               "or frame holds focus.", evidence=walk_cut_short(kb) or None)
