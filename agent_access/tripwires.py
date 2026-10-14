"""Tripwires: safety events recorded during a run (blocked writes, off-site navigation, refused actions, guardrail trips).

Everything goes into one list, `AuditContext.tripwires`, with one vocabulary. context.py's network_guard records each
blocked request (kinds "blocked_write" / "blocked_navigation", no "action"); most of those come from probes that test
forms on purpose. Guardrails and free-form interaction record theirs here with an "action", and only those are
reported. This module imports nothing from the rest of the package, so any layer can use it.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Literal

Action = Literal["blocked", "rejected", "halted", "warned"]

KINDS = (
    # network_guard (context.py) appends these itself: blocked_* for probes (action None, not reported), and
    # write_blocked / offsite_navigation for agent actions (network_guard(source="agent"), action "blocked").
    "blocked_write",
    "blocked_navigation",
    "write_blocked",        # an agent's click/fill/press/script sent a non-GET request --allow-submit doesn't allow
    "offsite_navigation",   # an agent's action tried to leave the site without --allow-offsite
    "destructive_action",   # click/press on something that buys, deletes, sends, or signs out (refused)
    "personal_data",        # fill_field with real-looking personal data (refused)
    "prompt_injection",     # page content that reads like instructions to the model (removed from the tool output)
    "no_page",              # an agent started with no page loaded
    "missing_evidence",     # a pass/fail judgement without evidence (refused)
    "conformance_claim",    # the summary claimed conformance the results don't support
    "review_incomplete",    # the reviewer skipped or invented finding ids
)
MAX_DETAIL = 300


def record_tripwire(audit, kind: str, detail: str, *, action: Action, agent: str | None = None,
                    tool: str | None = None) -> dict:
    """Append one tripwire record to audit.tripwires and return it."""
    assert kind in KINDS, kind
    record = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "page_url": audit.current_url,
        "owner": getattr(audit, "owner", None),
        "kind": kind,
        "detail": str(detail)[:MAX_DETAIL],
        "action": action,
        "agent": agent,
        "tool": tool,
    }
    audit.tripwires.append(record)
    return record


def tripwire_report(audit) -> dict:
    """The tripwires worth reporting: guardrail and interaction events, without the blocked requests that probes
    cause on purpose (network_guard records those without an "action")."""
    items = [t for t in audit.tripwires if t.get("action")]
    return {"counts": dict(Counter(t["kind"] for t in items)), "items": items}
