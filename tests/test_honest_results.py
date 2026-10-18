"""Phase 2 gate: no pass or not-applicable reaches the report unless a reviewer confirmed it, whatever went wrong on
the way (a missed verdict, a failed batch, a page or state that can't be rebuilt, a spent budget, no review at all, a
doubted failure followed by a pass, a probe that errored). No model calls."""

import asyncio

from test_agent import RouterModel, confirm_all

from agent_access.agent_tools.browser_tools import load_html_content
from agent_access.agent_tools.report_tools import add_manual_finding, compile_report
from agent_access.budget import Budget
from agent_access.context import AuditContext
from agent_access.engine import judge
from agent_access.models import ReviewResult, ReviewVerdict, Status
from agent_access.probes import ProbeResult
from agent_access.rules import TOOLS_IN_ORDER
from agent_access.team.reviewer import apply_verdicts, review_findings

DECIDED = (Status.PASS, Status.NOT_APPLICABLE)


def judged(page: str = "page") -> AuditContext:
    audit = AuditContext()
    audit.current_url = page
    add_manual_finding(audit, "2.4.2", "pass", "Descriptive title.", "Title is 'Contact us - Shop'.")
    add_manual_finding(audit, "1.2.2", "not_applicable", "No media.", "No video, audio or embeds in the page or tree.")
    return audit


def reported(audit: AuditContext, page: str = "page") -> dict:
    return {c.wcag_criterion: c.status for c in compile_report(audit, page).criteria}


def assert_honest(audit: AuditContext, page: str = "page") -> None:
    """Every reported pass or not-applicable rests on an agent verdict the reviewer confirmed."""
    for criterion, status in reported(audit, page).items():
        if status in DECIDED:
            agent = [f for f in audit.findings_for(page) if f.wcag_criterion == criterion and f.source == "agent"]
            assert agent and all(f.review_verdict == "confirmed" for f in agent if f.status is status), criterion


def test_confirmed_verdicts_are_reported_and_nothing_else_is():
    audit = judged()
    assert reported(audit)["2.4.2"] is Status.NEEDS_REVIEW and reported(audit)["1.2.2"] is Status.NEEDS_REVIEW  # no review
    apply_verdicts(audit.findings, ReviewResult(verdicts=[]))                                     # a missed verdict
    summary = {c.wcag_criterion: c.summary for c in compile_report(audit).criteria}["2.4.2"]
    assert reported(audit)["2.4.2"] is Status.NEEDS_REVIEW and "not reviewed" in summary
    apply_verdicts(audit.findings, ReviewResult(verdicts=[ReviewVerdict(finding_id=f.id, verdict="confirmed", reason="r")
                                                          for f in audit.findings]))
    assert reported(audit)["2.4.2"] is Status.PASS and reported(audit)["1.2.2"] is Status.NOT_APPLICABLE
    assert_honest(audit)


def test_a_doubted_failure_is_never_erased_by_a_later_pass():
    audit = judged()
    add_manual_finding(audit, "1.1.1", "fail", "Logo alt is 'img'.", "alt='img'", ["#logo"])
    fail = audit.findings[-1]
    apply_verdicts([fail], ReviewResult(verdicts=[ReviewVerdict(finding_id=fail.id, verdict="uncertain", reason="r")]))
    assert "error" in add_manual_finding(audit, "1.1.1", "pass", "Fine after all.", "The logo has alt text now.")
    assert reported(audit)["1.1.1"] is Status.NEEDS_REVIEW and fail in audit.findings


def test_unrebuildable_pages_failed_batches_and_a_spent_budget_leave_no_pass():
    def explode(_input):
        raise RuntimeError("never reached: the budget stops the reviewer before its first call")

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            page = (await load_html_content(audit, "<html lang='en'><title>Shop</title><body></body></html>"))["page_label"]
            for key in (page, f"{page} [menu-open]", "page"):   # a loadable page, a state with no steps, a lost page
                audit.set_current(key)
                add_manual_finding(audit, "2.4.2", "pass", "Descriptive title.", "Title is 'Shop'.")
            audit.budget = Budget(max_model_calls=1)
            audit.budget.spend()                                  # hard_exhausted: the reviewer can't make one call
            log, errors = await review_findings(audit, RouterModel({"reviewer": [explode]}))
            return audit, log
    audit, log = asyncio.run(scenario())
    assert {r["verdict"] for r in log} == {"not_reviewed"} and len(log) == 3
    for page in audit.pages:
        assert reported(audit, page)["2.4.2"] is Status.NEEDS_REVIEW
        assert_honest(audit, page)


def test_a_review_that_ran_confirms_and_the_report_shows_it():
    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            page = (await load_html_content(audit, "<html lang='en'><title>Shop</title><body></body></html>"))["page_label"]
            add_manual_finding(audit, "2.4.2", "pass", "Descriptive title.", "Title is 'Shop'.")
            await review_findings(audit, RouterModel({"reviewer": [confirm_all]}))
            return audit, page
    audit, page = asyncio.run(scenario())
    assert reported(audit, page)["2.4.2"] is Status.PASS
    assert_honest(audit, page)


def test_an_errored_probe_never_yields_pass_or_not_applicable():
    audit = AuditContext()
    audit.current_url = "page"
    decided = []
    for tool in TOOLS_IN_ORDER:
        ctx, _ = judge(audit, tool, {name: ProbeResult({}, "TimeoutError: page closed") for name in tool.uses})
        decided += [(tool.criterion, f.status) for f in ctx.findings if f.status in DECIDED]
    assert decided == []
