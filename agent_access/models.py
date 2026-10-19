"""Pydantic models shared by the audit tools and agents."""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NEEDS_REVIEW = "needs_review"
    # The page has no content the criterion applies to (e.g. no media for 1.2.x).
    NOT_APPLICABLE = "not_applicable"
    NOT_TESTED = "not_tested"


class Finding(BaseModel):
    """One piece of evidence about one WCAG criterion on one page."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    rule_id: str
    wcag_criterion: str
    status: Status
    source: str = Field(description='"axe", "custom", or "agent"')
    check_id: str = Field(description="axe rule id, custom check name, or 'manual'")
    page_url: str
    description: str
    selectors: list[str] = Field(default_factory=list)
    evidence: str | None = None
    # Set by the reviewer agent for failures and agent passes: "confirmed", "rejected", "uncertain", or "not_reviewed".
    review_verdict: str | None = None
    review_note: str | None = None
    # The status before the reviewer changed it (e.g. "fail" for a failure it sent to needs_review).
    original_status: Status | None = None


class CriterionResult(BaseModel):
    rule_id: str
    wcag_criterion: str
    title: str
    level: str
    status: Status
    summary: str
    findings: list[Finding] = Field(default_factory=list)


class AuditReport(BaseModel):
    page_url: str
    target_level: str
    counts: dict[str, int]
    criteria: list[CriterionResult]
    notes: list[str] = Field(default_factory=list)


class SiteReport(BaseModel):
    """All pages audited in one run, plus a combined per-criterion result (worst status across pages)."""

    target_level: str
    pages: list[AuditReport]
    counts: dict[str, int]
    criteria: list[CriterionResult]
    # Criteria above the conformance target (e.g. AAA when the target is AA): tested, reported separately.
    beyond_target: list[CriterionResult] = Field(default_factory=list)
    beyond_target_counts: dict[str, int] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class Recommendation(BaseModel):
    wcag_criterion: str
    issue: str
    fix: str
    priority: Literal["high", "medium", "low"]


class AuditSummary(BaseModel):
    """The summary writer's output. The authoritative per-criterion statuses come from the compiled report."""

    overall_assessment: str = Field(description="Two to four sentences on the page or site's accessibility.")
    top_issues: list[Recommendation] = Field(description="Most important problems with concrete fixes, worst first.")
    manual_review_needed: list[str] = Field(description="Things a human tester must still check, and why.")


class Judgement(BaseModel):
    """One agent judgement about one criterion on the loaded page (an entry for record_findings)."""

    criterion: str = Field(description='Criterion number such as "2.4.2".')
    status: Literal["pass", "fail", "needs_review", "not_applicable"]
    description: str = Field(description="One-sentence statement of the result.")
    evidence: str = Field(description="What you observed on the page (or in the check's details) that supports the status.")
    selectors: list[str] = Field(description="CSS selectors of the relevant elements; an empty list if none.")


class LeadNotes(BaseModel):
    """The lead auditor's final output: anything the report data alone doesn't say."""

    notes_for_human: list[str] = Field(description="Things you could not test or settle, and why.")


class PagePick(BaseModel):
    """The page picker's output: which same-site pages to audit."""

    urls: list[str] = Field(description="Chosen URLs, copied exactly from the candidate links.")


class SpecialistReport(BaseModel):
    """A specialist agent's output, returned to the lead auditor."""

    summary: str = Field(description="One to three sentences on what you tested and found.")
    criteria_recorded: list[str] = Field(description="Criterion numbers you recorded findings for.")
    unresolved: list[str] = Field(description="Things you could not settle, and why.")


class ReviewVerdict(BaseModel):
    finding_id: str
    verdict: Literal["confirmed", "rejected", "uncertain"]
    reason: str = Field(description="What you checked and what you saw.")
    defect_seen: bool | None = Field(default=None, description=(
        "Whether you saw a failure of this criterion on the page, whatever the item's status: a pass you confirm has "
        "false, a fail you confirm has true. null if you couldn't check."))
    fact_holds: bool | None = Field(default=None, description=(
        "For a measured failure (source axe or custom): whether what the check measured is true on the page (the "
        "element, attribute, text or value it names), whatever you think of the verdict. null for other items."))


class ReviewResult(BaseModel):
    verdicts: list[ReviewVerdict]
