"""Planning support for the lead auditor's loop: which criteria are unresolved, and what is left to try.

The lead calls get_open_items, runs one of the suggested tests, and repeats until `done` is true.

Every criterion needs an agent's judgement, not only the ones automation can't decide:
- a *judgement* rule (judgement=True) asks the agent to decide what automation can't (is the alt text meaningful?);
- a *measured* rule (judgement=False) asks the agent to confirm that the measurement covered the page's relevant
  content (did the Tab walk reach every control? did the contrast sampler see the text over the hero image?);
- an automated not_applicable verdict asks the agent to confirm nothing was missed (a video in an iframe, a canvas
  animation, a form-like widget without <form>).

A criterion is therefore "open" until it fails or an agent has recorded a judgement for it on that page. Each open
criterion names its next untried step (rerun its check, delegate to its specialist, or review it yourself). When an
agent has judged it and still recorded needs_review, it moves to `exhausted_for_human`: only a human can settle it.
"""

from __future__ import annotations

import json

from agents import RunContextWrapper, function_tool

from agent_access.context import AuditContext, no_page
from agent_access.models import Status
from agent_access.agent_tools.report_tools import _criterion_status
from agent_access.rules import REGISTRY, TOOLS_IN_ORDER

SPECIALIST_FOR_DOMAIN = {"forms": "audit_forms", "media": "audit_media", "navigation": "audit_navigation"}
# Agents run criteria's automated checks through the generic run_checks tool, so that's the
# test they see for a check that hasn't run on this page state.
RUN_CHECKS = "run_checks"
CHECK_NAMES = {t.name for t in TOOLS_IN_ORDER}
# Statuses that an agent still has to settle (a fail is settled: the reviewer verifies it instead).
UNSETTLED = (Status.NEEDS_REVIEW, Status.NOT_TESTED, Status.NOT_APPLICABLE)


def _plan(tool) -> list[str]:
    """A criterion's plan: its own check, then its specialist (if it has one), then the lead's own review.

    The lead's review comes last for every criterion, so a criterion the specialist skipped, and every measured
    or not-applicable result, still gets an agent's judgement.
    """
    plan = [tool.name]
    if tool.domain in SPECIALIST_FOR_DOMAIN:
        plan.append(SPECIALIST_FOR_DOMAIN[tool.domain])
    plan.append("review")
    return plan


# Built from the registry of 86 criterion tools. "review" means the lead judges it from evidence
# (get_criterion, outline, accessibility tree, screenshot, inspect_element) and records the judgement with
# record_finding(s); audit_forms / audit_media / audit_navigation delegate the judgement to a specialist first.
SUGGESTED_TESTS: dict[str, list[str]] = {t.criterion: _plan(t) for t in TOOLS_IN_ORDER}


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
    """The lead's view (domain None) covers every criterion at the target level and every test, including delegation.
    A specialist's view (domain given) covers its own criteria, and its own delegate tool counts as tried, so what it
    sees left is "review": judge it yourself. Each open criterion shows its next untried step."""
    page_url = page_url or audit.current_url
    if page_url is None:
        return no_page()
    findings = audit.findings_for(page_url)
    tried = set(audit.attempted_on(page_url))
    if domain is not None:
        tried.add(SPECIALIST_FOR_DOMAIN[domain])
    open_, exhausted = [], []
    for rule in audit.target_rules():
        if domain is not None and REGISTRY[rule.wcag_criterion].domain != domain:
            continue
        crit = [f for f in findings if f.wcag_criterion == rule.wcag_criterion]
        if is_exhausted(crit):
            exhausted.append(f"{rule.wcag_criterion} {rule.title}")
        elif is_open(crit):
            status, _ = _criterion_status(crit)
            # "review" is never marked tried: it is done once the lead records a judgement, which closes the item.
            nxt = next(t for t in SUGGESTED_TESTS[rule.wcag_criterion] if t not in tried)
            open_.append({"criterion": rule.wcag_criterion, "title": rule.title, "status": status.value,
                          "next_tests": [RUN_CHECKS if nxt in CHECK_NAMES else nxt]})

    # Group by test so the lead can settle several criteria with one call.
    by_test: dict[str, list[str]] = {}
    for item in open_:
        for t in item["next_tests"]:
            by_test.setdefault(t, []).append(item["criterion"])
    return {
        "page_url": page_url,
        "done": not open_,
        "open_count": len(open_),
        "tests_to_run": dict(sorted(by_test.items(), key=lambda kv: -len(kv[1]))),
        "open": open_,
        "exhausted_for_human": exhausted,
        # Lazy measurement: what the agent (or the code floor) still has to measure on this page state.
        "unmeasured": unmeasured_criteria(audit, page_url, domain),
        "measurement": getattr(audit, "measurement", None) or "eager",
    }


@function_tool
def get_open_items(ctx: RunContextWrapper[AuditContext]) -> str:
    """List the WCAG criteria on the current page that still need an agent's judgement, and the tests not tried yet.

    Every criterion stays open until it fails or an agent records a judgement for it: criteria that need judgement,
    criteria whose automated check passed (confirm the check covered the page), and criteria the check called
    not applicable (confirm nothing was missed).
    tests_to_run maps each untried test to the criteria it could settle; run the one that settles the most.
    "review" means judge those criteria yourself: get_criterion each one, then record_findings (any status counts).
    "run_checks" means those criteria's checks haven't run on this page state (unmeasured): call run_checks with the
    list, then judge the results. In lazy measurement nothing is measured until you ask.
    audit_forms / audit_media / audit_navigation means delegate those criteria to that specialist.
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
        applicable (confirm nothing was missed). "review" means get_criterion it, gather evidence, then record it
        (any status counts). "run_checks" means its check hasn't run on this page state yet (unmeasured): call
        run_checks with those criteria, then judge the results. Keep going until done.
        """
        return json.dumps(open_items(ctx.context, domain=domain))

    return get_domain_open_items
