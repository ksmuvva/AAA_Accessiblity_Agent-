"""The reviewer's independence: it judges an agent's pass on a judgement criterion blind (without the auditor's
reasoning), and when the auditors use a named model other than the SDK's default it reviews with the default.
No model calls."""

from agent_access.context import AuditContext
from agent_access.models import Finding, Status
from agent_access.rules import REGISTRY
from agent_access.team.orchestrator import default_reviewer_model
from agent_access.team.reviewer import REVIEWER_INSTRUCTIONS, _brief, is_blind


def finding(criterion: str, status: Status, source: str = "agent") -> Finding:
    return Finding(rule_id=f"WCAG_{criterion.replace('.', '_')}", wcag_criterion=criterion, status=status, source=source,
                   check_id="manual" if source == "agent" else REGISTRY[criterion].name, page_url="page",
                   description="The links are clear from their context.", evidence="Each follows a paragraph about it.",
                   selectors=["a"])


def test_a_judged_pass_is_reviewed_blind():
    audit = AuditContext()
    f = finding("2.4.4", Status.PASS)
    brief = _brief(audit, f)
    assert REGISTRY["2.4.4"].judgement and is_blind(f)
    assert brief["blind"] and "description" not in brief and "evidence" not in brief   # no anchoring on the auditor
    assert brief["question"] == list(audit.catalogue.get("2.4.4").manual_checks) and brief["selectors"] == ["a"]
    assert "automated_check" in brief                                                  # the measurement still shows


def test_measured_passes_and_failures_keep_the_auditors_reasoning():
    audit = AuditContext()
    measured_pass = finding("1.4.3", Status.PASS)          # contrast: a measured rule the auditor confirmed
    judged_fail = finding("2.4.4", Status.FAIL)
    measured_fail = finding("1.1.1", Status.FAIL, source="axe")
    assert not REGISTRY["1.4.3"].judgement
    for f in (measured_pass, judged_fail, measured_fail):
        brief = _brief(audit, f)
        assert not is_blind(f) and "blind" not in brief and brief["description"] and brief["evidence"]


def test_the_reviewer_is_told_how_to_decide_a_blind_pass():
    assert "marked blind" in REVIEWER_INSTRUCTIONS and "A general impression" in REVIEWER_INSTRUCTIONS


def test_the_reviewer_defaults_to_a_different_model_when_the_auditors_name_one():
    from agents.models import get_default_model

    default = get_default_model()
    assert default_reviewer_model("cheap-model") == default        # a different model reviews the cheap one's work
    assert default_reviewer_model(default) == default              # already the default: nothing different to pick
    assert default_reviewer_model(None) is None                    # SDK default for both
    scripted = object()                                            # a Model object (the tests' fake) is never swapped
    assert default_reviewer_model(scripted) is scripted
