"""Agentic measurement (lane G6): the agents choose which checks run and when; code guarantees the target is measured.

Two measurement modes, read from `audit.measurement` ("eager" is AuditContext's default; the CLI defaults to "lazy"):

    eager: measure_on_load runs the full sweep (every probe, all 86 rules) on every newly loaded page.
    lazy:  measure_on_load pre-collects only the cheap read-only probes (so the inspection tools have data) and runs no
           rule. Agents measure with run_checks(criteria) or run_all_checks().

The floor (code, not model): run_sweep_remaining runs every rule AT OR BELOW THE TARGET LEVEL that hasn't run on the
page state yet. The orchestrator or planner calls it before a page is declared done, so lazy mode changes who chooses
the order and the extras, never whether the conformance target is measured. Criteria above the target run only when an
agent asks for them; the ones nobody asked for stay not_tested and the report says they were not measured. open_items
keeps its contract (open until fail or an agent judgement) and additionally lists `unmeasured` target criteria, so the
agent, and the report's coverage warnings, can see what was never measured.

A probe with parameters (viewport, colour scheme, reduced motion) is G5's run_probe (agent_tools/verify_tools.py): a
retest or an explicit rerun updates the page's probe cache, and run_checks then judges the new data; results measured
under non-default options are never cached, so rules only ever see the page's default state.
"""

from __future__ import annotations

from typing import Literal

from agents import FunctionTool, RunContextWrapper, function_tool

from agent_access.context import AuditContext, call_tool, is_state_key
from agent_access.engine import DIRTY_WARNING, register_engines, run_rule, run_sweep, sweep_summary
from agent_access.agent_tools.planning import open_items, unmeasured_criteria
from agent_access.probes import collect
from agent_access.rules import REGISTRY, TOOLS_IN_ORDER

MEASUREMENT_MODES = ("eager", "lazy")
# Read-only probes that are cheap and that the lead's first look (outline, tree, screenshot) and the specialists'
# listings rely on. Lazy mode pre-collects these and nothing else.
CHEAP_PROBES = ("axe", "page", "instrumentation", "nav", "media")
MAX_FINDINGS_PER_RULE = 6


def measurement_mode(audit: AuditContext) -> str:
    """The run's measurement mode (the CLI validates the value). getattr until lane G3 adds the field to AuditContext."""
    return getattr(audit, "measurement", None) or "eager"


async def measure_on_load(audit: AuditContext) -> list[dict]:
    """What runs when a page (or a labelled state) has just been loaded. Eager: the full sweep. Lazy: only the cheap
    probes, no rules; the agents decide what to measure. Returns the rule runs it made."""
    if not audit.has_page():
        return []
    if measurement_mode(audit) == "eager":
        return await run_sweep(audit)
    for name in CHEAP_PROBES:
        await collect(audit, name)
    register_engines(audit)
    return []


def target_tools(audit: AuditContext) -> list:
    """The rules at or below the conformance target, in sweep order: what the floor guarantees is measured."""
    target = {r.wcag_criterion for r in audit.target_rules()}
    return [t for t in TOOLS_IN_ORDER if t.criterion in target]


async def run_sweep_remaining(audit: AuditContext, page_key: str | None = None) -> list[dict]:
    """The floor: run every target-level rule whose check hasn't run on `page_key` (default: the page cursor), then mark
    it swept. Rules above the target are left to the agents. Idempotent, and a no-op on a page whose target-level checks
    have all run. Returns the rule runs it made.

    Rules measure the DOM the browser is showing, so the floor first makes sure that is `page_key`: it reloads the page
    unless the browser is provably on it and it is clean. A labelled state (reached by interaction) can't be reloaded;
    if it still has unrun checks, nothing is measured and the gap stays visible as "never measured".
    Side effect: the browser may be left on `page_key` even if the caller's page cursor points elsewhere, so a caller
    that continues on another page must restore it from the live browser URL, not from the cursor.
    """
    page = page_key or audit.current_url
    if page is None or not audit.has_page():
        return []
    floor = target_tools(audit)
    if all(t.name in audit.attempted_on(page) for t in floor):
        audit.swept.add(page)
        return []
    if is_state_key(page):
        audit.warnings.append(f"{page}: this page state can't be re-created by reloading, so the checks that never ran "
                              "on it were not run by the measurement floor.")
        return []
    live = (await audit.page()).url == page and page not in audit.dirty  # inline pages show about:blank: reload them
    if not live and not await audit.restore_page(page):
        audit.warnings.append(f"{page}: the page could not be restored, so its remaining checks did not run.")
        return []
    before = len(audit.tool_runs)
    with audit.at_page(page):
        for tool in floor:
            if tool.name not in audit.attempted_on(page):
                await run_rule(audit, tool)
        audit.swept.add(page)
        register_engines(audit)
    return audit.tool_runs[before:]


def _compact(result: dict) -> dict:
    return {"criterion": result["criterion"], "result": result["result"],
            "findings": result["findings"][:MAX_FINDINGS_PER_RULE], "details": result["details"],
            "error": result["error"] or (("; ".join(result["probe_errors"])) or None),
            "your_judgement_needed": result["your_judgement_needed"]}


async def run_checks_now(audit: AuditContext, criteria: list[str]) -> dict:
    """Run the given criteria's checks on the loaded page (each collects only the probes its rule uses, cached per
    page), record their findings, and say what is still open and unmeasured."""
    wanted = [c for c in dict.fromkeys(c.strip() for c in criteria) if c in REGISTRY]
    unknown = [c for c in criteria if c.strip() not in REGISTRY]
    if not wanted:
        return {"error": f"No known criteria in {criteria}."}
    ran = [_compact(await run_rule(audit, REGISTRY[c])) for c in wanted]
    items = open_items(audit)
    result = {
        "ran": ran,
        "open_after": items["open_count"],
        "unmeasured_after": len(items["unmeasured"]),
        "next": ("Judge what you ran: get_criterion for details and evidence, then record_findings. "
                 + (f"{len(items['unmeasured'])} criteria at the target level are still unmeasured; run_checks them, or "
                    "run_all_checks." if items["unmeasured"] else "Every criterion at the target level has been measured.")),
    }
    if unknown:
        result["ignored"] = unknown
    if audit.current_url in audit.dirty:
        result["warning"] = DIRTY_WARNING
    return result


async def run_all_checks_now(audit: AuditContext) -> dict:
    """Run every probe and all 86 rules on the loaded page now (the eager sweep, on demand)."""
    summary = sweep_summary(await run_sweep(audit))
    items = open_items(audit)
    summary["open_after"] = items["open_count"]
    summary["unmeasured_after"] = len(items["unmeasured"])
    if audit.current_url in audit.dirty:
        summary["warning"] = DIRTY_WARNING
    return summary


def check_tools(criteria: list[str], *, include_all: bool) -> list[FunctionTool]:
    """run_checks over a scoped criterion enum, plus run_all_checks when `include_all`."""
    Criterion = Literal[tuple(criteria)]  # noqa: N806 - a type built at runtime

    async def run_checks(ctx: RunContextWrapper[AuditContext], criteria: list[str]) -> str:
        return await call_tool(ctx, run_checks_now, criteria)

    # Real objects, not strings: this module uses postponed annotations, and the enum is local.
    run_checks.__annotations__ = {"ctx": RunContextWrapper[AuditContext], "criteria": list[Criterion], "return": str}
    tools = [function_tool(run_checks, description_override=(
        "Run the automated checks for these criteria on the loaded page now and record their findings (replacing earlier "
        "ones). Each check measures only what its rule needs, so this is the cheap way to measure a few criteria before "
        "judging them. get_open_items lists unmeasured criteria under tests_to_run['run_checks']. Returns each check's "
        "result, findings, details and what you must judge; then read details with get_criterion and record_findings."))]
    if include_all:
        async def run_all_checks(ctx: RunContextWrapper[AuditContext]) -> str:
            return await call_tool(ctx, run_all_checks_now)

        tools.append(function_tool(run_all_checks, description_override=(
            "Run every probe and all 86 automated checks on the loaded page now (the full sweep), recording their findings. "
            "Use it when the page is simple or many criteria are unmeasured; otherwise run_checks for the ones you need. "
            "Returns totals, the checks with failures or review items, errors, and how much is still open.")))
    return tools


def check_tools_for(domain: str | None) -> list[FunctionTool]:
    """A specialist gets run_checks over its domain's criteria; the lead (domain None) gets run_checks over all 86 and
    run_all_checks. Attached next to criterion_tools_for(domain) by the team builders."""
    if domain is None:
        return check_tools([t.criterion for t in TOOLS_IN_ORDER], include_all=True)
    return check_tools([t.criterion for t in TOOLS_IN_ORDER if t.domain == domain], include_all=False)


__all__ = ["CHEAP_PROBES", "MEASUREMENT_MODES", "check_tools_for", "measure_on_load", "measurement_mode",
           "run_all_checks_now", "run_checks_now", "run_sweep_remaining", "target_tools"]
