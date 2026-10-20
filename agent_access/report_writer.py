"""Write audit results to disk as JSON (machine-readable) and Markdown (human-readable)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from agent_access.models import CriterionResult, Finding, Status

if TYPE_CHECKING:
    from agent_access.team.orchestrator import AuditOutcome

STATUS_LABEL = {
    Status.FAIL: "Fail",
    Status.NEEDS_REVIEW: "Needs review",
    Status.NOT_TESTED: "Not tested",
    Status.PASS: "Pass",
    Status.NOT_APPLICABLE: "Not applicable",
}
TABLE_ORDER = (Status.FAIL, Status.NEEDS_REVIEW, Status.NOT_TESTED, Status.PASS, Status.NOT_APPLICABLE)


def review_line(review_log: list[dict]) -> str:
    """One line on what the reviewer did with failures and with the passes and not-applicables agents judged."""
    parts = []
    for status, noun in (("fail", "failure"), ("pass", "judged pass"), ("not_applicable", "judged not-applicable")):
        group = [r for r in review_log if r.get("status", "fail") == status]
        if not group:
            continue
        demoted = sum(1 for r in group if r.get("demoted"))
        unreviewed = sum(1 for r in group if r["verdict"] == "not_reviewed")
        uncertain = sum(1 for r in group if r["verdict"] == "uncertain" and not r.get("demoted"))
        kept = len(group) - demoted - unreviewed - uncertain
        line = (f"{len(group)} {noun}(s) re-checked: {kept} kept, {demoted} moved to needs review"
                + (f", {uncertain} kept though the reviewer was uncertain" if uncertain else ""))
        parts.append(line + (f", {unreviewed} not reviewed" if unreviewed else ""))
    return "; ".join(parts) + "."


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _finding_lines(f: Finding, multi_page: bool) -> list[str]:
    where = f" on {f.page_url}" if multi_page else ""
    lines = [f"- **{f.check_id}**{where}: {f.description}"]
    if f.selectors:
        lines.append("  - Elements: " + ", ".join(f"`{s}`" for s in f.selectors[:5]))
    if f.evidence:
        lines.append(f"  - Evidence: {_md_escape(f.evidence)}")
    if f.review_verdict:
        lines.append(f"  - Reviewer ({f.review_verdict}): {_md_escape(f.review_note or '')}")
    return lines


def _result_text(findings: dict) -> str:
    return ", ".join(f"{n} {STATUS_LABEL[Status(s)].lower()}" for s, n in sorted(findings.items())) or "no findings"


def _tests_run(runs: list[dict], multi: bool) -> list[str]:
    """One row per criterion tool per page, including tools that found nothing."""
    lines = ["## Tests run", "",
             f"{len(runs)} criterion tool run(s): one tool per WCAG 2.2 success criterion. Every check at the target "
             "level runs in code on every page; with lazy measurement the agents choose when, and checks above the "
             "target run only if an agent asked for them.", ""]
    header = "| " + ("Page | " if multi else "") + "Criterion | Level | Tool | Technologies | Result | Time |"
    lines += [header, "|---" * (header.count("|") - 1) + "|"]
    for r in runs:
        result = f"Error: {_md_escape(r['error'] or '')}" if not r["ok"] else _md_escape(r.get("note") or _result_text(r["findings"]))
        page = f"{_md_escape(r['page_url'])} | " if multi else ""
        lines.append(f"| {page}{r['criteria']} | {r.get('level', '')} | `{r['tool']}` | {r.get('tech', '')} | {result} | {r['seconds']}s |")
    return lines + [""]


SCRIPT_PAGE_NOTE = {"reloaded": " (the script changed the page; it was reloaded)",
                    "left changed": " (the script changed the page and it could not be reloaded; later results may "
                                    "describe the changed page)"}


def _scripts(scripts: list[dict]) -> list[str]:
    """The read-only scripts the agents wrote and ran, so a reader can re-run the evidence a judgement cites."""
    lines = ["## Agent scripts", "",
             "Read-only scripts the agents ran in the page. A judgement that relies on one cites its id in its evidence.", ""]
    for s in scripts:
        got = f"error: {s['error']}" if s["error"] else f"result: {s['result']}"
        # A fence longer than any backtick run in the script, so the script can't close it.
        fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", s["function"])), default=0))
        lines += [f"### {s['id']} on {_md_escape(s['page_url'])}", "", fence + "js", s["function"], fence, "",
                  _md_escape(got) + SCRIPT_PAGE_NOTE.get(s["page_changed"], ""), ""]
    return lines


def _criteria_sections(criteria: list[CriterionResult], multi: bool, heading_prefix: str = "") -> list[str]:
    lines: list[str] = []
    for status, heading in ((Status.FAIL, "Failures"), (Status.NEEDS_REVIEW, "Needs review")):
        group = [c for c in criteria if c.status is status]
        if not group:
            continue
        lines += [f"## {heading_prefix}{heading}", ""]
        for c in group:
            lines += [f"### {c.wcag_criterion} {c.title} (Level {c.level})", "", c.summary, ""]
            for f in c.findings:
                if status is Status.FAIL and f.status is not Status.FAIL:
                    continue
                lines += _finding_lines(f, multi)
            lines.append("")
    return lines


def _plan_line(entry: dict) -> str:
    action = entry.get("action", "?")
    rest = ", ".join(f"{k}={v}" for k, v in entry.items() if k != "action" and v not in (None, [], ""))
    return f"{action}: {rest}" if rest else action


def render_markdown(outcome: AuditOutcome) -> str:
    report, summary = outcome.report, outcome.summary
    multi = len(report.pages) > 1
    lines = ["# WCAG 2.2 Accessibility Audit", ""]
    lines.append(f"**Conformance target:** WCAG 2.2 Level {report.target_level} ({len(report.criteria)} criteria)  ")
    lines.append("**Pages audited:**")
    lines += [f"- {p.page_url}" for p in report.pages]
    lines.append("")

    for e in outcome.errors:
        lines += [f"> **A stage did not finish:** {_md_escape(e)}. Results cover only the work completed.", ""]
    if outcome.warnings:
        lines += ["## Coverage warnings", "", "Parts of the audit the agents left incomplete. They are not passes.", ""]
        lines += [f"- {_md_escape(w)}" for w in outcome.warnings] + [""]

    if summary:
        lines += ["## Summary", "", summary.overall_assessment, ""]

    lines += [f"## Results by criterion (Level {report.target_level})", "", "| Status | Criteria |", "|---|---|"]
    lines += [f"| {STATUS_LABEL[s]} | {report.counts[s.value]} |" for s in TABLE_ORDER]
    lines.append("")
    if multi:
        lines += ["| Page | " + " | ".join(STATUS_LABEL[s] for s in TABLE_ORDER) + " |", "|---" * (len(TABLE_ORDER) + 1) + "|"]
        for p in report.pages:
            lines.append(f"| {_md_escape(p.page_url)} | " + " | ".join(str(p.counts[s.value]) for s in TABLE_ORDER) + " |")
        lines.append("")

    if outcome.engines:
        lines += ["## Technologies used", ""] + [f"- **{k}:** {_md_escape(v)}" for k, v in outcome.engines.items()] + [""]
    if sample := getattr(outcome, "sample", None):
        lines += ["## Sample", "", f"{sample['method']}. Seed {sample['seed']}, at most {sample['cap']} pages; "
                  f"{sample['candidates']} candidate pages ({sample['from_sitemap']} listed in /sitemap.xml) in "
                  f"{len(sample['templates'])} URL templates.", ""]
        lines += [f"- Structured: {_md_escape(u)}" for u in sample["structured"]]
        lines += [f"- Random: {_md_escape(u)}" for u in sample["random"]] + [""]
    if documents := getattr(outcome, "documents", []):
        lines += ["## Linked documents", "", "Same-site PDFs the audited pages link to, validated against PDF/UA-1 by "
                  "veraPDF. Their results are not part of the pages' criteria above.", ""]
        for d in documents:
            if not d["checked"]:
                lines.append(f"- {_md_escape(d['url'])}: not checked ({_md_escape(d['reason'])})")
                continue
            failed = d["failed_rules"]
            lines.append(f"- {_md_escape(d['url'])}: " + (f"meets PDF/UA-1 ({d['passed_rules']} rules passed)" if d["compliant"]
                         else f"{len(failed)} PDF/UA-1 rule(s) failed"))
            lines += [f"  - {r['clause']}-{r['test']}" + (f" (WCAG {', '.join(r['criteria'])})" if r["criteria"] else "")
                      + f": {_md_escape(r['description'])} ({r['checks']} check(s))" for r in failed]
        lines.append("")

    plan_log, budget = getattr(outcome, "plan_log", []), getattr(outcome, "budget_used", None)
    tripwires = getattr(outcome, "tripwires", [])
    if plan_log or budget:
        lines += ["## How the run was planned", ""]
        if budget:
            lines.append(f"**Budget:** {budget['model_calls']} of {budget['max_model_calls']} model calls, "
                         f"{budget['elapsed_minutes']} of {budget['max_minutes']:g} minutes, page cap {budget['max_pages']}"
                         + (" (working budget exhausted)" if budget.get("exhausted") else "") + "  ")
        if plan_log:
            lines.append("The planner agent's decisions, in order:")
            lines += [f"- {_md_escape(_plan_line(e))}" for e in plan_log]
        lines.append("")
    if tripwires:
        lines += ["## Tripwires", "", f"{len(tripwires)} guarded action(s) were blocked or flagged while the agents "
                  "worked.", ""]
        lines += [f"- {_md_escape(json.dumps(t, default=str)[:300])}" for t in tripwires[:20]] + [""]

    if outcome.review_log:
        lines += [f"Reviewer: {review_line(outcome.review_log)}", ""]

    if summary and summary.top_issues:
        lines += ["## Top issues and fixes", ""]
        for i, rec in enumerate(summary.top_issues, 1):
            lines += [f"{i}. **{rec.wcag_criterion}** ({rec.priority} priority): {rec.issue}", f"   - Fix: {rec.fix}"]
        lines.append("")

    lines += _criteria_sections(report.criteria, multi)

    if summary and summary.manual_review_needed:
        lines += ["## Manual review still needed", ""] + [f"- {item}" for item in summary.manual_review_needed] + [""]
    if outcome.lead_notes:
        lines += ["## Auditor notes", ""] + [f"- {n}" for n in outcome.lead_notes] + [""]

    for status, heading, intro in (
        (Status.NOT_TESTED, "Not tested", "These criteria were not evaluated. They must not be treated as passing."),
        (Status.NOT_APPLICABLE, "Not applicable", "The audited pages have no content these criteria apply to."),
        (Status.PASS, "Passed (reviewer confirmed)", ""),
    ):
        group = [c for c in report.criteria if c.status is status]
        if group:
            lines += [f"## {heading}", ""] + ([intro, ""] if intro else [])
            lines += [f"- {c.wcag_criterion} {c.title} (Level {c.level})" for c in group] + [""]

    if report.beyond_target:
        b = report.beyond_target_counts
        # From the check runs, not the aggregated status: a check that ran on one page and not another still ran.
        ran = {r["criteria"] for r in outcome.tool_runs}
        unmeasured = sum(1 for c in report.beyond_target if c.wcag_criterion not in ran)
        tested = len(report.beyond_target) - unmeasured
        lines += [f"## Beyond the Level {report.target_level} target", "",
                  f"{tested} higher-level criteria were also tested"
                  + (f"; {unmeasured} were not measured on any page, because no agent chose to run their checks"
                     if unmeasured else "")
                  + f". They don't affect Level {report.target_level} conformance.", "",
                  "| Status | Criteria |", "|---|---|"] + [f"| {STATUS_LABEL[s]} | {b.get(s.value, 0)} |" for s in TABLE_ORDER] + [""]
        lines += _criteria_sections(report.beyond_target, multi, heading_prefix="Beyond target: ")

    if outcome.tool_runs:
        lines += _tests_run(outcome.tool_runs, multi)
    if getattr(outcome, "scripts", None):
        lines += _scripts(outcome.scripts)

    lines += ["## Notes", ""] + [f"- {n}" for n in report.notes] + [""]
    return "\n".join(lines)


def write_reports(out_dir: Path, outcome: AuditOutcome) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "report.json"
    md_path = out_dir / "report.md"
    payload = {
        "summary": outcome.summary.model_dump(mode="json") if outcome.summary else None,
        "lead_notes": outcome.lead_notes,
        "review_log": outcome.review_log,
        "engines": outcome.engines,
        "tool_runs": outcome.tool_runs,
        "scripts": getattr(outcome, "scripts", []),
        "warnings": outcome.warnings,
        "errors": outcome.errors,
        "plan_log": getattr(outcome, "plan_log", []),
        "budget_used": getattr(outcome, "budget_used", None),
        "tripwires": getattr(outcome, "tripwires", []),
        "memory": getattr(outcome, "memory", None),
        "sample": getattr(outcome, "sample", None),
        "documents": getattr(outcome, "documents", []),
        "report": outcome.report.model_dump(mode="json"),
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(outcome), encoding="utf-8")
    return json_path, md_path
