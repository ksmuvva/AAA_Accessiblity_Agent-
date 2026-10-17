"""Tools for recording manual findings and compiling audit reports."""

from __future__ import annotations

import json
from typing import Literal

from agents import RunContextWrapper, function_tool

from agent_access.context import AuditContext, no_page
from agent_access.models import AuditReport, CriterionResult, Finding, Judgement, SiteReport, Status

# Worst status first; used to combine per-page results into a site-wide result.
SEVERITY = [Status.FAIL, Status.NEEDS_REVIEW, Status.NOT_TESTED, Status.PASS, Status.NOT_APPLICABLE]


def add_manual_finding(
    audit: AuditContext,
    criterion: str,
    status: str,
    description: str,
    evidence: str,
    selectors: list[str] | None = None,
) -> dict:
    if audit.current_url is None:
        return no_page()
    if criterion.strip() in audit.catalogue.removed_criteria:
        return {"error": f"{criterion} is obsolete and removed in WCAG 2.2; do not record findings for it."}
    rule = audit.catalogue.get(criterion)
    if rule is None:
        return {"error": f"Unknown WCAG 2.2 criterion: {criterion}"}
    if not evidence.strip():
        return {"error": "Evidence is required: describe what you observed on the page."}
    new_status = Status(status)
    earlier = [f for f in audit.findings if f.page_url == audit.current_url and f.source == "agent"
               and f.wcag_criterion == rule.wcag_criterion]
    earlier_fails = [f for f in earlier if f.status is Status.FAIL]
    if earlier_fails and new_status in (Status.PASS, Status.NOT_APPLICABLE):
        return {"error": f"A failure is already recorded for {rule.wcag_criterion} on this page "
                         f"({earlier_fails[0].description!r}). The reviewer decides whether failures stand; "
                         "record needs_review if you now doubt it, or another fail for a further defect."}
    # A new judgement replaces earlier agent judgements with a different status (findings with the same
    # status accumulate, e.g. one fail per defective element). Agent failures are never replaced.
    superseded = {id(f) for f in earlier if f.status is not new_status and f.status is not Status.FAIL}
    audit.findings = [f for f in audit.findings if id(f) not in superseded]
    finding = Finding(
        rule_id=rule.rule_id,
        wcag_criterion=rule.wcag_criterion,
        status=new_status,
        source="agent",
        check_id="manual",
        page_url=audit.current_url,
        description=description,
        selectors=selectors or [],
        evidence=evidence,
    )
    audit.add_findings([finding])
    return {"recorded": finding.wcag_criterion, "status": finding.status.value, "page_url": audit.current_url,
            **({"replaced_earlier_judgements": len(superseded)} if superseded else {})}


def _criterion_status(findings: list[Finding]) -> tuple[Status, str]:
    """Combine all findings for one criterion.

    Precedence: fail > agent pass > needs_review > not_applicable > automated pass (-> needs_review) > not_tested.
    A failure the reviewer sent to needs_review still blocks an agent pass: doubt about a failure must never
    turn into a pass.
    """
    fails = [f for f in findings if f.status is Status.FAIL]
    if fails:
        return Status.FAIL, f"{len(fails)} failing check(s)."
    demoted = [f for f in findings if f.original_status is Status.FAIL]
    if demoted:
        return Status.NEEDS_REVIEW, f"{len(demoted)} reported failure(s) could not be confirmed on review; a human must decide."
    # A manual review by the agent can resolve automated "needs review" results, but never a failure.
    if any(f.status is Status.PASS and f.source == "agent" for f in findings):
        return Status.PASS, "Passed on review."
    if any(f.status is Status.NEEDS_REVIEW for f in findings):
        return Status.NEEDS_REVIEW, "Some checks could not be decided automatically; manual review required."
    if any(f.status is Status.NOT_APPLICABLE for f in findings):
        return Status.NOT_APPLICABLE, "No content on the page that this criterion applies to."
    if any(f.status is Status.PASS for f in findings):
        return Status.NEEDS_REVIEW, "Automated checks passed; manual verification still required."
    return Status.NOT_TESTED, "Not tested."


def _counts(criteria: list[CriterionResult]) -> dict[str, int]:
    return {s.value: sum(1 for c in criteria if c.status is s) for s in Status}


def report_notes(audit: AuditContext) -> list[str]:
    beyond = len(audit.beyond_target_rules())
    return [
        "Criteria with status not_tested were not evaluated and must not be treated as passing.",
        "Automated checks alone cannot prove conformance; criteria that only passed automated checks are marked needs_review.",
        f"The conformance target is WCAG 2.2 Level {audit.target_level} ({len(audit.target_rules())} criteria)."
        + (f" The {beyond} criteria above the target are reported separately"
           + ("; in lazy measurement only the ones an agent chose to check were tested." if audit.measurement == "lazy"
              else " and were also tested.") if beyond else ""),
        "Full conformance also requires evaluating complete pages and complete processes.",
    ]


def _criterion_results(audit: AuditContext, page_url: str, rules) -> list[CriterionResult]:
    page_findings = audit.findings_for(page_url)
    results = []
    for rule in rules:
        rule_findings = [f for f in page_findings if f.wcag_criterion == rule.wcag_criterion]
        status, summary = _criterion_status(rule_findings)
        results.append(CriterionResult(
            rule_id=rule.rule_id, wcag_criterion=rule.wcag_criterion, title=rule.title, level=rule.level,
            status=status, summary=summary,
            # Keep the report compact: automated passing evidence is only counted, not listed.
            findings=[f for f in rule_findings if f.status is not Status.PASS or f.source == "agent"],
        ))
    return results


def compile_report(audit: AuditContext, page_url: str | None = None) -> AuditReport | None:
    page_url = page_url or audit.current_url
    if page_url is None:
        return None
    criteria = _criterion_results(audit, page_url, audit.target_rules())
    return AuditReport(page_url=page_url, target_level=audit.target_level, counts=_counts(criteria),
                       criteria=criteria, notes=report_notes(audit))


def _combine(per_page_results: list[tuple[str, list[CriterionResult]]], rules) -> list[CriterionResult]:
    combined = []
    for i, rule in enumerate(rules):
        per_page = [(url, results[i]) for url, results in per_page_results]
        worst = min((c.status for _, c in per_page), key=SEVERITY.index)
        affected = [url for url, c in per_page if c.status is worst]
        summary = (f"{worst.value} on {len(affected)} of {len(per_page)} page(s)." if len(per_page) > 1
                   else per_page[0][1].summary)
        combined.append(CriterionResult(
            rule_id=rule.rule_id, wcag_criterion=rule.wcag_criterion, title=rule.title, level=rule.level,
            status=worst, summary=summary, findings=[f for _, c in per_page for f in c.findings],
        ))
    return combined


def compile_site_report(audit: AuditContext) -> SiteReport | None:
    """Combine every audited page. A criterion takes its worst status across pages.

    `criteria` covers the conformance target; `beyond_target` holds the higher-level criteria, also tested.
    """
    pages = [r for r in (compile_report(audit, p) for p in audit.pages) if r is not None]
    if not pages:
        return None
    target = audit.target_rules()
    combined = _combine([(p.page_url, p.criteria) for p in pages], target)
    beyond_rules = audit.beyond_target_rules()
    beyond = (_combine([(p.page_url, _criterion_results(audit, p.page_url, beyond_rules)) for p in pages], beyond_rules)
              if beyond_rules else [])
    return SiteReport(target_level=audit.target_level, pages=pages, counts=_counts(combined), criteria=combined,
                      beyond_target=beyond, beyond_target_counts=_counts(beyond) if beyond else {},
                      notes=report_notes(audit))


@function_tool
def record_finding(
    ctx: RunContextWrapper[AuditContext],
    criterion: str,
    status: Literal["pass", "fail", "needs_review", "not_applicable"],
    description: str,
    evidence: str,
    selectors: list[str],
) -> str:
    """Record your own judgement about one WCAG criterion on the loaded page.

    Use this for criteria you assessed yourself (for example from get_page_outline), not for tool
    results, which are recorded automatically. Only record "pass" when the evidence actually supports it:
    the reviewer re-checks every pass and failure you record. Recording a different status later replaces
    your earlier judgement for that criterion, except a fail, which only the reviewer can overturn.
    Recording any status for a criterion also tells get_open_items that you have reviewed it.

    Args:
        criterion: Criterion number such as "2.4.2".
        status: "pass", "fail", "needs_review" when a human must decide, or "not_applicable" when the page
            has no content the criterion applies to (say what you checked in evidence).
        description: One-sentence statement of the result.
        evidence: What you observed on the page that supports the status.
        selectors: CSS selectors of the relevant elements; use an empty list if none.
    """
    return json.dumps(add_manual_finding(ctx.context, criterion, status, description, evidence, selectors))


def add_manual_findings(audit: AuditContext, judgements: list[Judgement]) -> dict:
    """Record several judgements at once (one per criterion); each is checked like record_finding."""
    results = [add_manual_finding(audit, j.criterion, j.status, j.description, j.evidence, j.selectors)
               for j in judgements]
    return {"recorded": [r for r in results if "error" not in r], "errors": [r for r in results if "error" in r]}


@function_tool
def record_findings(ctx: RunContextWrapper[AuditContext], judgements: list[Judgement]) -> str:
    """Record your judgements for several WCAG criteria on the loaded page in one call.

    Same rules as record_finding, one entry per criterion. Use it after you have read each criterion with
    get_criterion and gathered the evidence: e.g. confirm a batch of measured checks whose details show they
    covered the page, or a batch of not-applicable results you checked. Every entry needs its own evidence;
    the reviewer re-checks every pass and failure.
    """
    return json.dumps(add_manual_findings(ctx.context, judgements))


def recorded_findings(audit: AuditContext, criteria: list[str] | None = None) -> dict:
    if audit.current_url is None:
        return no_page()
    wanted = {c.strip() for c in criteria or [] if c.strip()}
    findings = [f for f in audit.findings_for(audit.current_url) if not wanted or f.wcag_criterion in wanted]
    return {
        "page_url": audit.current_url,
        "findings": [
            {"criterion": f.wcag_criterion, "status": f.status.value, "source": f"{f.source}/{f.check_id}",
             "description": f.description, "selectors": f.selectors[:5], "evidence": (f.evidence or "")[:300] or None}
            for f in findings
        ][:80],
    }


@function_tool
def get_recorded_findings(ctx: RunContextWrapper[AuditContext], criteria: list[str]) -> str:
    """Read the findings already recorded for the current page, including the automated tests' results.

    Use this instead of re-running a test when you need its details (e.g. the error messages a form showed).

    Args:
        criteria: Criterion numbers to filter by, e.g. ["3.3.1", "3.3.3"]. Pass an empty list for all.
    """
    return json.dumps(recorded_findings(ctx.context, criteria))
