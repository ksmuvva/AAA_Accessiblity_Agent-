"""Summary writer: turns the reviewed report into the plain-language AuditSummary."""

from __future__ import annotations

import json

from agents import Agent, RunConfig, Runner
from agents.models.interface import Model

from agent_access.context import AuditContext
from agent_access.models import AuditSummary, SiteReport, Status
from agent_access.team.guardrails import apply_guardrails

SUMMARY_INSTRUCTIONS = """\
You are the summary writer on a WCAG 2.2 audit team (conformance target: Level {level}). The input is the
final, reviewed audit data.
Write an AuditSummary for the site owner and their developers:
- overall_assessment: two to four plain sentences. Say it is not a full conformance claim when any
  criteria are needs_review or not_tested.
- top_issues: the confirmed failures only, worst first, each with a concrete, developer-ready fix that
  names the element or pattern.
- manual_review_needed: what a human tester must still check, and why. Combine related criteria.
Mention any coverage_warnings: they mark parts of the audit that are incomplete.
Use only the data given. Do not invent issues, and never describe not_tested criteria as passing.
"""


def build_summarizer(model: str | Model | None = None, level: str = "AA") -> Agent:
    """The summary writer, with its output guardrail (no conformance claim): a trip raises an AgentsException, which
    the orchestrator records as a failed summary stage."""
    return apply_guardrails(Agent(name="Summary writer", instructions=SUMMARY_INSTRUCTIONS.replace("{level}", level),
                                  model=model, output_type=AuditSummary))


def summary_input(report: SiteReport, lead_notes: list[str], review_log: list[dict],
                  warnings: list[str] | None = None) -> str:
    def brief(status: Status) -> list[dict]:
        return [
            {"criterion": f"{c.wcag_criterion} {c.title}", "level": c.level, "summary": c.summary,
             "issues": sorted({f"{f.description}" + (f" [{', '.join(f.selectors[:3])}]" if f.selectors else "")
                               for f in c.findings if f.status is status})[:5]}
            for c in report.criteria if c.status is status
        ]

    return json.dumps({
        "pages": [p.page_url for p in report.pages],
        "counts": report.counts,
        "confirmed_failures": brief(Status.FAIL),
        "needs_review": brief(Status.NEEDS_REVIEW),
        "not_tested": [f"{c.wcag_criterion} {c.title}" for c in report.criteria if c.status is Status.NOT_TESTED],
        "lead_auditor_notes": lead_notes,
        "coverage_warnings": warnings or [],
        "failures_rejected_by_reviewer": [r for r in review_log if r.get("status", "fail") == "fail"
                                          and r["verdict"] in ("rejected", "uncertain")],
        "passes_not_confirmed_by_reviewer": [r for r in review_log if r.get("status") == "pass"
                                             and r["verdict"] in ("rejected", "uncertain")],
    }, indent=1)


async def write_summary(report: SiteReport, lead_notes: list[str], review_log: list[dict],
                        model: str | Model | None = None, *, audit: AuditContext, warnings: list[str] | None = None,
                        run_config: RunConfig | None = None, writer: Agent | None = None) -> AuditSummary:
    """Write the summary with `writer` (e.g. the team's guarded summary writer) or a fresh one for the report's level.
    `audit` is the run context: the output guardrail reads its findings to decide whether a conformance claim holds."""
    writer = writer or build_summarizer(model, report.target_level)
    result = await Runner.run(writer, summary_input(report, lead_notes, review_log, warnings), context=audit,
                              max_turns=3, run_config=run_config)
    return result.final_output
