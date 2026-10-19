"""Planning support for the lead auditor's loop: which criteria are unresolved, and what is known about each.

The lead calls get_open_items, decides what to do next, and repeats until `done` is true. get_open_items reports the
page's state, not a route: it never says which test to run next. The agent chooses the order and the method (measure,
delegate to a specialist, or judge it itself); code still decides when the page is done.

Every criterion needs an agent's judgement, not only the ones automation can't decide:
- a *judgement* rule (judgement=True) asks the agent to decide what automation can't (is the alt text meaningful?);
- a *measured* rule (judgement=False) asks the agent to confirm that the measurement covered the page's relevant
  content (did the Tab walk reach every control? did the contrast sampler see the text over the hero image?);
- an automated not_applicable verdict asks the agent to confirm nothing was missed (a video in an iframe, a canvas
  animation, a form-like widget without <form>).

A criterion is therefore "open" until it fails or an agent has recorded a judgement for it on that page. Each open
criterion shows its status, whether its check has run on this page state, and what the check found. When an agent has
judged it and still recorded needs_review, it moves to `exhausted_for_human`: only a human can settle it.
"""

from __future__ import annotations

import json
from collections import Counter

from agents import RunContextWrapper, function_tool

from agent_access.context import AuditContext, no_page
from agent_access.models import Status
from agent_access.agent_tools.report_tools import _criterion_status, inherited
from agent_access.rules import REGISTRY

SPECIALIST_FOR_DOMAIN = {"forms": "audit_forms", "media": "audit_media", "navigation": "audit_navigation"}
# Statuses that an agent still has to settle (a fail is settled: the reviewer verifies it instead).
UNSETTLED = (Status.NEEDS_REVIEW, Status.NOT_TESTED, Status.NOT_APPLICABLE)


def is_open(findings) -> bool:
    """True while a criterion still needs an agent's judgement on this page: it hasn't failed, and no agent has
    recorded anything for it. An agent's needs_review settles it as far as agents go (a human must decide)."""
    status, _ = _criterion_status(findings)
    return status in UNSETTLED and not any(f.source == "agent" for f in findings)


def is_exhausted(findings) -> bool:
    """An agent judged it and still couldn't decide: only a human tester can settle it."""
    status, _ = _criterion_status(findings)
    return status is Status.NEEDS_REVIEW and any(f.source == "agent" for f in findings)


def unmeasured_criteria(audit: AuditContext, page_url: str | None = None, domain: str | None = None) -> list[str]:
    """Criteria at the target level whose automated check has not run on this page state (lazy measurement, or a
    state labelled after interaction). The code floor (check_tools.run_sweep_remaining) empties this before a page
    is declared done; until then the agent can measure them with run_checks."""
    page_url = page_url or audit.current_url
    tried = audit.attempted_on(page_url) if page_url else set()
    return [rule.wcag_criterion for rule in audit.target_rules()
            if REGISTRY[rule.wcag_criterion].name not in tried
            and (domain is None or REGISTRY[rule.wcag_criterion].domain == domain)]


def open_items(audit: AuditContext, page_url: str | None = None, domain: str | None = None) -> dict:
    """The page's state. The lead's view (domain None) covers every criterion at the target level and shows which
    specialist owns which open criteria and whether it has been tried on this page; a specialist's view (domain given)
    covers its own criteria. Each open criterion shows its status, whether its check has run on this page state
    (`measured`), and the automated check's results (`automated`, counts by status). No next step is prescribed."""
    page_url = page_url or audit.current_url
    if page_url is None:
        return no_page()
    findings = audit.findings_for(page_url)
    tried = audit.attempted_on(page_url)
    open_, exhausted = [], []
    specialists: dict[str, dict] = {}
    same_as_page = 0
    for rule in audit.target_rules():
        tool = REGISTRY[rule.wcag_criterion]
        if domain is not None and tool.domain != domain:
            continue
        if inherited(audit, page_url, rule.wcag_criterion):
            same_as_page += 1
            continue
        crit = [f for f in findings if f.wcag_criterion == rule.wcag_criterion]
        if is_exhausted(crit):
            exhausted.append(f"{rule.wcag_criterion} {rule.title}")
        elif is_open(crit):
            status, _ = _criterion_status(crit)
            open_.append({"criterion": rule.wcag_criterion, "title": rule.title, "status": status.value,
                          "measured": tool.name in tried,
                          "automated": dict(Counter(f.status.value for f in crit if f.source != "agent"))})
            if domain is None and tool.domain in SPECIALIST_FOR_DOMAIN:
                owner = SPECIALIST_FOR_DOMAIN[tool.domain]
                specialists.setdefault(owner, {"tried": owner in tried, "open": []})["open"].append(rule.wcag_criterion)
    items = {
        "page_url": page_url,
        "done": not open_,
        "open_count": len(open_),
        "open": open_,
        "exhausted_for_human": exhausted,
        # Lazy measurement: what the agent (or the code floor) still has to measure on this page state.
        "unmeasured": unmeasured_criteria(audit, page_url, domain),
        "measurement": getattr(audit, "measurement", None) or "eager",
    }
    if same_as_page:   # a labelled state: what it didn't change takes the result of the page it was reached from
        items["inherited"] = same_as_page
    if domain is None:
        items["specialists"] = specialists
    return items


@function_tool
def get_open_items(ctx: RunContextWrapper[AuditContext]) -> str:
    """List the WCAG criteria on the current page that still need an agent's judgement, and what is known about each.

    Every criterion stays open until it fails or an agent records a judgement for it: criteria that need judgement,
    criteria whose automated check passed (confirm the check covered the page), and criteria the check called
    not applicable (confirm nothing was missed).
    Each open item shows its status, `measured` (whether its automated check has run on this page state) and
    `automated` (that check's results by status). `specialists` lists, per specialist, the open criteria in its area
    and whether it has been tried on this page. `unmeasured` lists target criteria whose check hasn't run.
    In a labelled state, criteria whose checks found the same as on the page it was reached from take that page's
    result and are not listed (`inherited` counts them).
    It does not tell you what to do next: decide the order and the method yourself (measure with run_checks,
    delegate an area to its specialist, or judge criteria yourself with get_criterion and record_findings).
    Keep going until done is true. Criteria in exhausted_for_human need a human tester.
    """
    return json.dumps(open_items(ctx.context))


def open_items_tool(domain: str):
    """A specialist's get_open_items: the criteria in its domain that still need its judgement."""

    @function_tool(name_override="get_open_items")
    def get_domain_open_items(ctx: RunContextWrapper[AuditContext]) -> str:
        """List the WCAG criteria in your area on the current page that still need your judgement.

        A criterion stays open until it fails or you record a judgement for it: criteria that need judgement,
        criteria whose automated check passed (confirm the check covered the page), and criteria the check called not
        applicable (confirm nothing was missed). Each open item shows its status, `measured` (whether its check has
        run on this page state; if not, run_checks it before judging) and `automated` (the check's results). Read it
        with get_criterion, gather evidence, then record it (any status counts). Keep going until done.
        """
        return json.dumps(open_items(ctx.context, domain=domain))

    return get_domain_open_items
