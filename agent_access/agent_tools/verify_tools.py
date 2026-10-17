"""Self-verification tools: an agent measures again before it trusts (or records) a result.

    run_probe       re-run any probe now, optionally on given elements or under other conditions (viewport, colour
                    scheme, reduced motion), and see its raw data
    evaluate_js     run a read-only script in the page (writes and navigation blocked; the page is restored if the
                    script changed it)
    verify_finding  re-measure one criterion without changing anything: does a fresh measurement reproduce its
                    recorded fails/passes, and do the elements a judgement names still exist?

How run_probe treats the probe cache (the rules only ever judge default-state data):
    retestable probe + targets, no options  -> engine.retest: merged into the cache, its rules re-run
    no options, rerun_rules=True            -> replaces the cached result, every rule that uses it re-runs
    no options, rerun_rules=False           -> returned only, so the agent can compare it with the sweep's result
    with options                            -> returned only (evidence for the agent), never cached

Not imported by agent_tools/__init__: the engine imports agent_tools.report_tools, so importing the engine from the
package init would be circular. Import the tools from this module.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Literal

from agents import RunContextWrapper, function_tool
from pydantic import BaseModel, Field

from agent_access.context import AuditContext, call_tool, no_page
from agent_access.probes import PROBE_ORDER, PROBES, ProbeResult
from agent_access.rules import REGISTRY, TOOLS_IN_ORDER

MAX_LIST_ITEMS = 25
MAX_STRING_CHARS = 500
MAX_RESULT_CHARS = 12_000
MAX_JS_RESULT_CHARS = 6_000
MAX_LOGGED_RESULT_CHARS = 2_000  # what the script log keeps for the report and the reviewer
JS_TIMEOUT_S = 5
MAX_VERIFIED_SELECTORS = 5
MAX_VERIFIED_FINDINGS = 10
FINGERPRINT_JS = """() => {
  const html = document.documentElement.outerHTML;
  let h = 0;
  for (let i = 0; i < html.length; i++) h = (h * 31 + html.charCodeAt(i)) | 0;
  return [html.length, h, location.href];
}"""

ProbeName = Literal[tuple(PROBE_ORDER)]  # type: ignore[valid-type]
CriterionId = Literal[tuple(t.criterion for t in TOOLS_IN_ORDER)]  # type: ignore[valid-type]


class ProbeOptions(BaseModel):
    """Conditions to measure under; null leaves the page's default. Applied around the probe and reset afterwards."""

    viewport_width: int | None = Field(description="CSS pixels, e.g. 320 for reflow; null for the default 1280.")
    viewport_height: int | None = Field(description="CSS pixels, e.g. 256; null for the default 800.")
    color_scheme: Literal["light", "dark"] | None = Field(description="prefers-color-scheme to emulate, or null.")
    reduced_motion: Literal["reduce", "no-preference"] | None = Field(description="prefers-reduced-motion, or null.")


def _trim(value, depth: int = 0):
    """Shorten probe data for the model: long lists and strings are cut, deep nesting summarised."""
    if isinstance(value, str):
        return value if len(value) <= MAX_STRING_CHARS else value[:MAX_STRING_CHARS] + "..."
    if depth > 6:
        return "..."
    if isinstance(value, dict):
        return {k: _trim(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        items = [_trim(v, depth + 1) for v in value[:MAX_LIST_ITEMS]]
        if len(value) > MAX_LIST_ITEMS:
            items.append(f"... {len(value) - MAX_LIST_ITEMS} more")
        return items
    return value


def _compact(data) -> dict | str:
    trimmed = _trim(data)
    text = json.dumps(trimmed, default=str)
    return trimmed if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + "... (truncated)"


def _options_dict(options: ProbeOptions | dict | None) -> dict:
    if options is None:
        return {}
    raw = options.model_dump() if isinstance(options, BaseModel) else dict(options)
    return {k: v for k, v in raw.items() if v is not None}


async def _apply_options(audit: AuditContext, opts: dict) -> dict:
    """Apply measurement conditions; returns what _reset_options needs."""
    page = await audit.page()
    previous = {"viewport": page.viewport_size}
    if "viewport_width" in opts or "viewport_height" in opts:
        size = dict(page.viewport_size or {"width": 1280, "height": 800})
        size["width"] = int(opts.get("viewport_width", size["width"]))
        size["height"] = int(opts.get("viewport_height", size["height"]))
        await page.set_viewport_size(size)
    media = {k: opts[k] for k in ("color_scheme", "reduced_motion") if k in opts}
    if media:
        await page.emulate_media(**media)
    return previous


async def _reset_options(audit: AuditContext, opts: dict, previous: dict) -> None:
    page = await audit.page()
    if ("viewport_width" in opts or "viewport_height" in opts) and previous.get("viewport"):
        await page.set_viewport_size(previous["viewport"])
    media = {k: "no-override" for k in ("color_scheme", "reduced_motion") if k in opts}
    if media:
        await page.emulate_media(**media)


async def measure_probe(audit: AuditContext, probe: str, targets=(), options: ProbeOptions | dict | None = None,
                        rerun_rules: bool = False) -> dict:
    """Run probe `probe` again on the current page now (see the module docstring for what is cached)."""
    from agent_access.engine import rerun_rules_using, retest  # lazy: the engine imports this package

    if not audit.has_page():
        return no_page()
    if probe not in PROBES:
        return {"error": f"Unknown probe {probe!r}. Probes: {', '.join(PROBE_ORDER)}."}
    spec = PROBES[probe]
    targets = [t for t in (targets or []) if t and t.strip()]
    opts = _options_dict(options)
    page_key = audit.current_url
    result: dict = {"probe": probe, "page_url": page_key, "options": opts or None}
    notes = []
    if targets and not spec.retestable:
        notes.append(f"The {probe} probe measures the whole page; targets were ignored.")
        targets = []
    if targets and not opts:
        return {**result, "mode": "retest", "cached": True, **(await retest(audit, probe, targets))}
    if rerun_rules and opts:
        notes.append("Measured under non-default conditions, so the result was not cached and no checks were re-run.")

    previous = await _apply_options(audit, opts) if opts else {}
    try:
        with audit.at_page(page_key):  # interactive probes restore the page and may move the cursor
            data = await (spec.run(audit, targets=targets) if targets else spec.run(audit))
        error = data.get("error") if isinstance(data, dict) else None
    except Exception as exc:
        data, error = {}, f"{type(exc).__name__}: {str(exc)[:200]}"
    finally:
        if opts:
            try:
                await _reset_options(audit, opts, previous)
            except Exception as exc:  # A closed page can't be reset; say so rather than hide the measurement.
                notes.append(f"Could not reset the measurement conditions: {exc}")

    cached = bool(rerun_rules and not opts and not error)
    if cached:
        audit.observations.setdefault(page_key, {})[probe] = ProbeResult(data, None)
        with audit.at_page(page_key):
            result["rules_rerun"] = await rerun_rules_using(audit, probe)
    result.update({"mode": "measure", "cached": cached, "error": error, "measured": _compact(data)})
    if notes:
        result["notes"] = notes
    return result


async def evaluate_script(audit: AuditContext, function: str) -> dict:
    """Run `function` (a JS function or expression) in the page, read-only: writes and navigations are blocked, and
    if the script changed the DOM the page is restored afterwards (or marked changed when it can't be)."""
    if not audit.has_page():
        return no_page()
    page = await audit.page()
    page_key = audit.current_url
    before = await page.evaluate(FINGERPRINT_JS)
    result: dict = {"page_url": page_key}
    async with audit.network_guard(source="agent", tool="evaluate_js") as blocked:
        try:
            value = await asyncio.wait_for(page.evaluate(function), timeout=JS_TIMEOUT_S)
            text = json.dumps(value, default=str)
            result["result"] = (value if len(text) <= MAX_JS_RESULT_CHARS
                                else text[:MAX_JS_RESULT_CHARS] + "... (truncated)")
        except asyncio.TimeoutError:
            result["error"] = f"The script did not finish within {JS_TIMEOUT_S} s."
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    if blocked:
        result["blocked_requests"] = blocked[:10]
    try:
        after = await page.evaluate(FINGERPRINT_JS)
    except Exception:
        after = None
    if after != before:
        # Restore outside the guard: inside it the reload itself would be answered with a 204.
        restored = await audit.restore_page(page_key) if audit.can_restore(page_key) else False
        if not restored:
            audit.dirty.add(page_key)
        result["page_changed"] = ("The script changed the page; it was reloaded to its tested state." if restored else
                                  "The script changed the page and it could not be reloaded; results now describe a "
                                  "changed page.")
    script_id = f"S{len(audit.scripts) + 1}"
    audit.scripts.append({"id": script_id, "page_url": page_key, "function": function,
                          "result": json.dumps(result.get("result"), default=str)[:MAX_LOGGED_RESULT_CHARS],
                          "error": result.get("error"), "page_changed": "page_changed" in result})
    result["script_id"] = script_id
    result["cite"] = (f"If a judgement relies on this result, put 'script {script_id}' in its evidence: the reviewer "
                      "re-runs the scripts a judgement cites.")
    return result


SCRIPT_CITATION = re.compile(r"\bscript\s+(S\d+)\b", re.I)


def cited_scripts(audit: AuditContext, *texts: str | None) -> list[dict]:
    """The logged scripts a judgement's description or evidence cites ("script S3"), in citation order."""
    by_id = {s["id"]: s for s in audit.scripts}
    ids = dict.fromkeys(m.group(1).upper() for t in texts if t for m in SCRIPT_CITATION.finditer(t))
    return [by_id[i] for i in ids if i in by_id]


def _brief(f) -> dict:
    return {"id": f.id, "criterion": f.wcag_criterion, "status": f.status.value, "source": f.source,
            "check": f.check_id, "description": f.description, "selectors": f.selectors[:6],
            "evidence": (f.evidence or "")[:300] or None}


async def _fresh_rule_run(audit: AuditContext, tool) -> tuple[list, list[str], dict]:
    """Measure the rule's probes afresh and judge, WITHOUT recording anything or touching the cache."""
    from agent_access.engine import judge  # lazy: the engine imports this package

    probes = {}
    with audit.at_page(audit.current_url):
        for name in tool.uses:
            try:
                data = await PROBES[name].run(audit)
                probes[name] = ProbeResult(data, data.get("error") if isinstance(data, dict) else None)
            except Exception as exc:
                probes[name] = ProbeResult({}, f"{type(exc).__name__}: {str(exc)[:200]}")
        ctx, error = judge(audit, tool, probes)
    return ctx.findings, ctx.probe_errors + ([f"rule: {error}"] if error else []), ctx.details


def _reproduces(original, fresh: list) -> bool:
    same = [f for f in fresh if f.status == original.status]
    if not original.selectors:
        return any(f.description == original.description or not f.selectors for f in same)
    return any(set(original.selectors) & set(f.selectors) for f in same)


async def verify(audit: AuditContext, criterion: str, finding_id: str = "", selectors=()) -> dict:
    """Re-measure one criterion's recorded findings (or one of them) on the loaded page, and inspect `selectors`
    (e.g. those of a judgement not recorded yet). One fresh measurement serves them all. Records nothing."""
    from agent_access.agent_tools.browser_tools import inspect

    if not audit.has_page():
        return no_page()
    if criterion not in REGISTRY:
        return {"error": f"Unknown criterion {criterion!r}."}
    page_key = audit.current_url
    findings = [f for f in audit.findings_for(page_key) if f.wcag_criterion == criterion]
    if finding_id:
        findings = [f for f in findings if f.id == finding_id]
        if not findings:
            return {"error": f"No finding {finding_id!r} for {criterion} on this page; pass an empty finding_id to "
                             "verify all of its findings."}
    tool = REGISTRY[criterion]
    fresh, errors, details = await _fresh_rule_run(audit, tool)
    result: dict = {"criterion": f"{criterion} {tool.title}", "page_url": page_key,
                    "fresh_measurement": {"check": tool.name, "findings": [_brief(f) for f in fresh[:10]],
                                          "errors": errors or None, "details": _compact(details)}}
    verified = []
    for f in [f for f in findings if f.status.value in ("fail", "pass")][:MAX_VERIFIED_FINDINGS]:
        # A judgement can't be re-measured: its selectors are inspected below and the fresh run is the evidence.
        reproduced = None if f.source == "agent" or errors else _reproduces(f, fresh)
        verified.append({**_brief(f), "reproduced": reproduced})
    result["recorded_findings"] = verified
    wanted = list(dict.fromkeys([*selectors, *(s for f in findings if f.source == "agent" for s in f.selectors)]))
    checked = []
    for selector in [s for s in wanted if s and s.strip()][:MAX_VERIFIED_SELECTORS]:
        info = await inspect(audit, selector)
        checked.append({"selector": selector, "matches": info.get("matches", 0), "error": info.get("error"),
                        "first": _trim((info.get("elements") or [None])[0]),
                        "accessibility_of_first": info.get("accessibility_of_first")})
    if checked:
        result["selectors"] = checked
    not_reproduced = [v["id"] for v in verified if v["reproduced"] is False]
    missing = [c["selector"] for c in checked if not c["matches"]]
    advice = [
        f"Not reproduced on a fresh measurement: {', '.join(not_reproduced)}. Call run_checks to replace the cached "
        "result, or explain the difference." if not_reproduced else "",
        f"Selectors that match nothing now: {', '.join(missing)}." if missing else "",
        "The fresh measurement failed, so automated findings could not be re-checked." if errors else "",
        "Agent judgements can't be re-measured: compare their claims with the fresh measurement and the elements "
        "above, and record a corrected judgement if they disagree." if any(v["source"] == "agent" for v in verified)
        else "",
    ]
    result["advice"] = " ".join(a for a in advice if a) or "Everything checked agrees with the fresh measurement."
    return result


@function_tool
async def run_probe(ctx: RunContextWrapper[AuditContext], probe: ProbeName, targets: list[str],  # type: ignore[valid-type]
                    options: ProbeOptions, rerun_rules: bool) -> str:
    """Measure the loaded page again with one probe and see its raw data: to check a result yourself, to re-test
    specific elements, or to measure under other conditions (e.g. viewport_width 320, dark colour scheme).

    Args:
        probe: The probe to run (e.g. "contrast", "reflow", "keyboard", "forms", "hover", "disclosures").
        targets: CSS selectors to re-test, only for forms, hover and disclosures (merged into the results, and their
            checks re-run); an empty list measures the whole page.
        options: Conditions to measure under; all null for the page's defaults. Measurements under other conditions
            are returned as evidence only and never change recorded results.
        rerun_rules: With default conditions: true replaces the cached measurement and re-runs every check that uses
            it; false only returns the data so you can compare it with get_criterion's details.
    """
    return await call_tool(ctx, measure_probe, probe, targets, options, rerun_rules)


@function_tool
async def evaluate_js(ctx: RunContextWrapper[AuditContext], function: str) -> str:
    """Run a read-only JavaScript function in the loaded page and return its JSON result, to measure something no
    tool covers (computed styles, element geometry, attribute values, a widget's states). Requests that write or
    navigate are blocked, the script times out after 5 s, and if it changes the page the page is reloaded. Return
    plain data, not elements. Each run gets an id (S1, S2, ...): cite it in a judgement's evidence as "script S2", so
    the reviewer can re-run it. A script result is your evidence, not an automated check: it never closes a criterion
    by itself.

    Args:
        function: A JavaScript function, e.g. "() => getComputedStyle(document.querySelector('a')).outlineStyle".
    """
    return await call_tool(ctx, evaluate_script, function)


@function_tool
async def verify_finding(ctx: RunContextWrapper[AuditContext], criterion: CriterionId,  # type: ignore[valid-type]
                         finding_id: str, selectors: list[str]) -> str:
    """Re-measure a criterion on the loaded page before relying on a result or recording a pass/fail. Runs its
    check afresh (nothing is recorded or replaced) and reports, for each recorded fail/pass, whether a fresh
    measurement reproduces it; for agent judgements and the selectors you pass, whether the elements still exist
    and how assistive technology sees them.

    Args:
        criterion: Criterion number such as "1.4.3".
        finding_id: One finding's id to verify, or an empty string for all of the criterion's findings on this page.
        selectors: Extra CSS selectors to inspect, e.g. those of the judgement you are about to record; may be empty.
    """
    return await call_tool(ctx, verify, criterion, finding_id, selectors)


SELF_VERIFICATION_TOOLS = [run_probe, evaluate_js, verify_finding]
