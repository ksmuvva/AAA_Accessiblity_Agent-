"""Engine: runs the probes (agent_access/probes) and rules (agent_access/rules) on a page, and exposes them to the agents.

    sweep:   every probe once, then all 86 rules          -> run_sweep (on every newly loaded page)
    rule:    collect the probes it uses, judge, record    -> run_rule (also the per-criterion agent tool)
    re-test: re-run a probe on given elements, merge the
             data into the cache, re-run its rules        -> retest (test_forms, test_hover_content, ...)
    site:    re-run cross-page rules once all pages exist -> run_site_tests

Every rule run is logged in AuditContext.tool_runs and shown in the report's "Tests run" section.

Agents don't get one tool per rule. They get generic tools over an enum of the criteria that agent handles:
get_criterion(criterion) to read, and run_checks(criteria) (agent_tools/check_tools.py) to measure
(criterion_tools_for). What an agent must judge comes from the rules catalogue (manual_checks), for every criterion:
judgement rules ask what automation can't decide, measured rules ask the agent to confirm the measurement covered
the page, and a not-applicable result asks it to confirm nothing was missed.
"""

from __future__ import annotations

import time
from collections import Counter
from importlib.metadata import version
from typing import Literal

from agents import FunctionTool, RunContextWrapper, function_tool

from agent_access.context import AuditContext, call_tool, is_state_key
from agent_access.models import Status
from agent_access.probes import PROBE_ORDER, PROBES, collect
from agent_access.agent_tools.report_tools import _criterion_status
from agent_access.rules import REGISTRY, TOOLS_IN_ORDER, Ctx, WcagTool

# Criteria that compare pages; re-run on every page once all pages are audited.
SITE_CRITERIA = ("3.2.3", "3.2.4", "3.2.6")
MAX_RETURNED_FINDINGS = 15


def _counts(findings) -> dict:
    return dict(Counter(f.status.value for f in findings))


NO_EVIDENCE = "No automated evidence either way; a manual check is needed."
CHECK_FAILED = ("The automated check could not run on this page (its measurement failed), so there is no automated "
                "evidence; judge from the page directly and say in your evidence that the check did not run.")
MEASURED_PREFIX = ("This criterion is measured by the check; its result is not final until you confirm the measurement "
                   "covered the page's relevant content. ")
NOT_APPLICABLE_PREFIX = ("The check found nothing this criterion applies to; confirm from the page (screenshot, "
                         "accessibility tree, outline) that it missed nothing, then record not_applicable or judge it. ")


def judgement_question(audit: AuditContext, tool: WcagTool, findings=()) -> str:
    """What an agent must judge for this criterion: the catalogue's manual_checks, else the rule's docstring.

    Every criterion needs an agent's judgement. For a judgement rule the question is what automation can't decide.
    For a measured rule it is whether the measurement covered the page, so the text says so. When the check found
    nothing applicable, the agent must confirm that too.
    """
    checks = audit.catalogue.get(tool.criterion).manual_checks
    question = " ".join(str(c) for c in checks) if checks else tool.doc
    statuses = {f.status for f in findings}
    if statuses and statuses <= {Status.NOT_APPLICABLE}:
        return NOT_APPLICABLE_PREFIX + question
    if not tool.judgement:
        return MEASURED_PREFIX + question
    return question


# Rules whose patterns are English words ("skip", "help", "left of", "confirm"): on another language, finding nothing
# proves nothing.
ENGLISH_PATTERNS = {"1.3.3", "2.4.1", "3.2.5", "3.2.6", "3.3.3", "3.3.4", "3.3.7"}


def judge(audit: AuditContext, tool: WcagTool, probes: dict) -> tuple[Ctx, str | None]:
    """Apply one rule to its probes' data (synchronous: nothing is awaited, recorded, or cached). Returns the rule's
    Ctx and the error if the rule raised. run_rule passes the cached data; verify_finding passes fresh data."""
    ctx = Ctx(audit, tool, probes)
    try:
        tool.impl(ctx)
        if "axe" in probes and not any(f.source == "axe" for f in ctx.findings):
            ctx.axe()   # a rule that doesn't read axe itself still reports what axe found for its criterion
        if "alfa" in probes and not any(f.status is Status.FAIL for f in ctx.findings):
            ctx.alfa()   # a second engine's failures, as questions, where the criterion hasn't failed already
    except Exception as exc:  # A broken rule must not stop the sweep.
        return ctx, f"{type(exc).__name__}: {str(exc)[:200]}"
    # A probe that failed or stopped early saw only part of the page (or none): an empty result there proves nothing.
    incomplete = ctx.probe_errors + [f"{n}: stopped early" for n, p in probes.items() if p.data.get("stopped_early")]
    lang = (probes["page"].data.get("page_lang") or "en").lower() if "page" in probes else "en"
    if tool.criterion in ENGLISH_PATTERNS and not lang.startswith("en"):
        incomplete.append(f"its word patterns are English and the page is lang={lang[:10]}")
    for f in ctx.findings if incomplete else ():
        if f.status in (Status.PASS, Status.NOT_APPLICABLE):
            f.status = Status.NEEDS_REVIEW
            f.description += f" [Not established: {'; '.join(incomplete)[:200]}]"
    return ctx, None


async def run_rule(audit: AuditContext, tool: WcagTool) -> dict:
    """Run one rule on the current page: collect its probes, judge, record its findings, and log the run."""
    page_key = audit.current_url
    start = time.monotonic()
    with audit.at_page(page_key):
        ctx, error = judge(audit, tool, {name: await collect(audit, name) for name in tool.uses})
        if not ctx.findings:
            # Always record something, so a criterion never ends "not tested" after a sweep. When the rule raised or a
            # probe failed, say so: an agent judging from this must know the measurement never happened.
            if error or ctx.probe_errors:
                ctx.review(f"{CHECK_FAILED} {'; '.join([error] if error else ctx.probe_errors)[:300]}")
            else:
                ctx.review(NO_EVIDENCE)
        audit.replace_findings(page_key, None, ctx.findings, tool.name)
        audit.mark_attempted(tool.name)
    audit.rule_details.setdefault(page_key, {})[tool.name] = ctx.details
    counts = _counts(ctx.findings)
    audit.tool_runs.append({
        "page_url": page_key, "tool": tool.name, "target": None, "criteria": tool.criterion,
        "level": tool.level, "tech": ", ".join(tool.tech), "ok": error is None and not ctx.probe_errors,
        "error": error or ("; ".join(ctx.probe_errors) or None), "findings": counts,
        "seconds": round(time.monotonic() - start, 2),
    })
    return {
        "criterion": f"{tool.criterion} {tool.title} (Level {tool.level})",
        "tool": tool.name,
        "technologies": list(tool.tech),
        "result": counts,
        "findings": [{"status": f.status.value, "description": f.description, "selectors": f.selectors[:4],
                      "evidence": (f.evidence or "")[:300] or None} for f in ctx.findings[:MAX_RETURNED_FINDINGS]],
        "details": ctx.details,
        "error": error,
        "probe_errors": ctx.probe_errors,
        "your_judgement_needed": judgement_question(audit, tool, ctx.findings),
    }


async def run_sweep(audit: AuditContext) -> list[dict]:
    """Run every probe, then every rule, on the current page. Returns this sweep's run log."""
    if not audit.has_page():
        return []
    page_url = audit.current_url
    for name in PROBE_ORDER:
        await collect(audit, name)
    before = len(audit.tool_runs)
    for tool in TOOLS_IN_ORDER:
        await run_rule(audit, tool)
    audit.swept.add(page_url)
    register_engines(audit)
    return audit.tool_runs[before:]


def register_engines(audit: AuditContext) -> None:
    """Record the measurement engines and versions used, for the report's Technologies section."""
    audit.engines.setdefault("Playwright", f"Playwright {version('playwright')} (Chromium)")
    audit.engines.setdefault("Colour Contrast Analyser method",
                             "WCAG 2 relative-luminance ratio (CCA formula) + pixel eyedropper for text over images")
    axe = audit.observations.get(audit.current_url, {}).get("axe")
    if axe is not None and not axe.error and axe.data.get("version"):
        audit.engines["axe-core"] = f"axe-core {axe.data['version']}"
    alfa = audit.observations.get(audit.current_url, {}).get("alfa")
    if alfa is not None and not alfa.error and alfa.data.get("version"):
        audit.engines["Siteimprove Alfa"] = f"Siteimprove Alfa {alfa.data['version']} (second opinion: questions only)"


async def run_site_tests(audit: AuditContext) -> None:
    """Re-run the cross-page rules on every audited page, now that every page's navigation has been captured.

    They compare cached navigation snapshots, so the browser stays where it is; only the page cursor moves.
    """
    pages = [p for p in audit.pages if p in audit.swept and not is_state_key(p)]   # a state is not another page
    if len(pages) < 2:
        return
    target = {r.wcag_criterion for r in audit.target_rules()}
    for page in pages:
        with audit.at_page(page):
            for criterion in SITE_CRITERIA:
                # Above the target only if its check already ran on the page (the eager sweep, or an agent asked).
                if criterion in target or REGISTRY[criterion].name in audit.attempted_on(page):
                    await run_rule(audit, REGISTRY[criterion])


def sweep_summary(runs: list[dict]) -> dict:
    """Compact view for the model: what ran and what it found."""
    totals = Counter()
    for r in runs:
        totals.update(r["findings"])
    problems = [f"{r['criteria']} {r['tool']}: " + ", ".join(f"{v} {k}" for k, v in r["findings"].items())
                for r in runs if r["findings"].get("fail") or r["findings"].get("needs_review")]
    return {
        "criterion_tools_run": len(runs),
        "finding_totals": dict(totals),
        "tools_with_failures_or_review_items": problems[:60],
        "errors": [f"{r['tool']}: {r['error']}" for r in runs if not r["ok"]][:10],
        "next": "Every criterion tool has run and recorded its findings. Use get_recorded_findings to read details, "
                "then call get_open_items and work on what's left.",
    }


# ---------------------------------------------------------------- re-tests on specific elements

def _identity(item):
    """Which element a probe entry describes, so a re-test replaces the old entry instead of duplicating it."""
    if isinstance(item, dict):
        return item.get("trigger") or item.get("form_selector") or item.get("form"), item.get("mode")
    return item


def merge(cached: dict, new: dict) -> None:
    """Fold re-test data into a probe's cached data: list entries for the same element are replaced."""
    for key, value in new.items():
        if isinstance(value, list) and isinstance(cached.get(key), list):
            ids = {_identity(v) for v in value}
            cached[key] = [v for v in cached[key] if _identity(v) not in ids] + value
        else:
            cached.setdefault(key, value)


async def retest(audit: AuditContext, probe_name: str, targets: list[str]) -> dict:
    """Re-run a retestable probe on `targets`, merge its data into the page's cache, and re-run the rules that
    read it, so the new evidence is judged exactly like the sweep's."""
    probe = PROBES[probe_name]
    assert probe.retestable, probe_name
    targets = [t for t in targets if t and t.strip()]
    if not targets:
        return {"error": "Pass at least one selector."}
    cached = await collect(audit, probe_name)
    with audit.at_page(audit.current_url):
        try:
            new = await probe.run(audit, targets=targets)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    merge(cached.data, new)
    return {"measured": new, "rules_rerun": await rerun_rules_using(audit, probe_name)}


async def rerun_rules_using(audit: AuditContext, probe_name: str) -> list[dict]:
    """Re-run every rule that reads `probe_name` on the current page; a short summary of each run."""
    rules = [await run_rule(audit, t) for t in TOOLS_IN_ORDER if probe_name in t.uses]
    return [{"criterion": r["criterion"], "result": r["result"],
             "findings": [f"{f['status']}: {f['description']}" for f in r["findings"][:6]]} for r in rules]


# ---------------------------------------------------------------- agent tools

DIRTY_WARNING = ("The page changed through interaction after it was tested, so these results describe the page as it "
                 "was loaded. Call label_page_state to test the current state.")


def criterion_view(audit: AuditContext, criterion: str) -> dict:
    """Everything an agent needs to settle one criterion on the current page, without re-measuring anything:
    the rule, its current status, its recorded findings (automated and agent), and what to judge."""
    tool, rule = REGISTRY[criterion], audit.catalogue.get(criterion)
    page_key = audit.current_url
    findings = [f for f in audit.findings_for(page_key) if f.wcag_criterion == criterion]
    status, summary = _criterion_status(findings)
    view = {
        "criterion": f"{criterion} {tool.title} (Level {tool.level})",
        "status": status.value,
        "status_summary": summary,
        "check": tool.name,
        "technologies": list(tool.tech),
        "findings": [{"status": f.status.value, "source": f.source, "description": f.description,
                      "selectors": f.selectors[:4], "evidence": (f.evidence or "")[:300] or None}
                     for f in findings[:MAX_RETURNED_FINDINGS]],
        "details": audit.rule_details.get(page_key, {}).get(tool.name, {}),
        "understanding_url": rule.understanding_url,
        "measured_only": not tool.judgement,
        "your_judgement_needed": judgement_question(audit, tool, [f for f in findings if f.source != "agent"]),
    }
    if tool.name not in audit.attempted_on(page_key):
        view["note"] = "The automated check hasn't run on this page state yet. Call run_checks to run it."
    last_run = next((r for r in reversed(audit.tool_runs) if r["page_url"] == page_key and r["tool"] == tool.name), None)
    if last_run and not last_run["ok"]:
        # The agent must not judge blind: the check raised, or a probe it relies on failed (common on real sites).
        view["automated_check_error"] = (f"The automated check did not complete on this page: {last_run['error']}. "
                                         "Its findings may be missing or partial; judge from the page directly and say so.")
    if page_key in audit.dirty:
        view["warning"] = DIRTY_WARNING
    return view


def criterion_tools(criteria: list[str], listed: list[str] | None = None) -> list[FunctionTool]:
    """get_criterion, accepting only `criteria` (a JSON-schema enum). `listed` criteria are named with their titles
    in the description, so the model can map topics to numbers. Measuring is check_tools.run_checks (same enum)."""
    Criterion = Literal[tuple(criteria)]  # noqa: N806 - a type built at runtime
    names = "; ".join(f"{c} {REGISTRY[c].title}" for c in (listed if listed is not None else criteria))

    async def get_criterion(ctx: RunContextWrapper[AuditContext], criterion: str) -> str:
        return await call_tool(ctx, criterion_view, criterion)

    # Real objects, not strings: this module uses postponed annotations, and the enum is local.
    get_criterion.__annotations__ = {"ctx": RunContextWrapper[AuditContext], "criterion": Criterion, "return": str}
    return [
        function_tool(get_criterion, description_override=(
            "Read one WCAG 2.2 criterion on the loaded page: its current status, the automated check's findings, "
            "evidence and details (if its check has run), your team's judgements so far, and what you must judge "
            "(your_judgement_needed: decide what automation can't, or confirm that a measured check or a not-applicable "
            f"result covered the page). Fast; measures nothing: run_checks does. Criteria in your area: {names}.")),
    ]


def criterion_tools_for(domain: str | None) -> list[FunctionTool]:
    """An agent's criterion tools: get_criterion (read) and run_checks (measure; the lead also gets run_all_checks),
    over the same enum. A specialist gets its domain's criteria. The lead (domain None) may reach any criterion,
    because it judges what specialists leave open, but only its own area is listed by name."""
    from agent_access.agent_tools.check_tools import check_tools_for  # check_tools imports this module

    if domain is None:
        read = criterion_tools([t.criterion for t in TOOLS_IN_ORDER],
                               [t.criterion for t in TOOLS_IN_ORDER if t.domain == "lead"])
    else:
        read = criterion_tools([t.criterion for t in TOOLS_IN_ORDER if t.domain == domain])
    return [*read, *check_tools_for(domain)]


async def _forms_listing(audit: AuditContext) -> dict:
    return {"page_url": audit.current_url, "forms": (await collect(audit, "forms")).data.get("forms", [])}


@function_tool
async def list_forms(ctx: RunContextWrapper[AuditContext]) -> str:
    """List the page's forms and their fields: labels, types, autocomplete tokens, required state, and selectors."""
    return await call_tool(ctx, _forms_listing)


@function_tool
async def test_forms(ctx: RunContextWrapper[AuditContext], form_selectors: list[str]) -> str:
    """Re-test specific forms the automated run missed: submit each empty and with invalid values (requests are
    blocked, so nothing is sent), and change its selects/checkboxes/radios. The 3.2.2, 3.3.x, and 4.1.3 rules
    are re-run on the new evidence.

    Args:
        form_selectors: CSS selectors of the forms (or form-like containers) to test, from list_forms.
    """
    return await call_tool(ctx, retest, "forms", form_selectors)


@function_tool
async def test_hover_content(ctx: RunContextWrapper[AuditContext], selectors: list[str]) -> str:
    """Re-test elements that show content on hover or focus (tooltips, mega-menus) for 1.4.13: is the content
    hoverable, persistent, and dismissible with Escape?

    Args:
        selectors: CSS selectors of the trigger elements.
    """
    return await call_tool(ctx, retest, "hover", selectors)


@function_tool
async def test_disclosures(ctx: RunContextWrapper[AuditContext], selectors: list[str]) -> str:
    """Re-test menus, accordions, and dialog buttons the automated run missed: do they open from the keyboard,
    update aria-expanded, move focus into dialogs, close with Escape, and return focus?

    Args:
        selectors: CSS selectors of the trigger elements.
    """
    return await call_tool(ctx, retest, "disclosures", selectors)
