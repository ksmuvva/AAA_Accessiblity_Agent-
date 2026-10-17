"""The planning loop's bookkeeping: what is open, what is exhausted, and how status aggregation treats it.

The coverage contract: on every page, every criterion at the target level stays open until it fails or an agent has
recorded a judgement for it, whatever its automated check said (needs review, passed, or not applicable).
"""

from agent_access.context import AuditContext
from agent_access.models import Finding, Judgement, Status
from agent_access.agent_tools.planning import SPECIALIST_FOR_DOMAIN, open_items
from agent_access.agent_tools.report_tools import add_manual_finding, add_manual_findings, compile_report
from agent_access.rules import REGISTRY


def automated(criterion: str, status: Status) -> Finding:
    tool = REGISTRY[criterion]
    return Finding(rule_id=f"WCAG_{criterion.replace('.', '_')}", wcag_criterion=criterion, status=status, source="custom",
                   check_id=tool.name, page_url="page", description=f"{tool.name} said {status.value}")


def swept(level: str = "AA") -> AuditContext:
    """A page after the sweep: every check tried and every criterion with an automated pass."""
    audit = AuditContext(target_level=level)
    audit.set_current("page")
    for tool in REGISTRY.values():
        audit.mark_attempted(tool.name)
        audit.add_findings([automated(tool.criterion, Status.PASS)])
    return audit


def fresh_audit(level: str = "AA") -> AuditContext:
    audit = AuditContext(target_level=level)
    audit.set_current("page")
    return audit


def test_open_items_report_state_not_a_route():
    """get_open_items describes the page; it never prescribes the next test. The agent plans the order itself."""
    items = open_items(swept())
    assert "tests_to_run" not in items
    assert all(set(i) == {"criterion", "title", "status", "measured", "automated"} for i in items["open"])
    assert all(i["measured"] for i in items["open"])  # the sweep ran every check


def test_automated_pass_or_not_applicable_stays_open_until_an_agent_judges():
    audit = swept()
    audit.replace_findings("page", None, [automated("1.2.2", Status.NOT_APPLICABLE)], "check_prerecorded_captions")
    items = open_items(audit)
    assert items["open_count"] == 55 and not items["exhausted_for_human"]
    by = {i["criterion"]: i for i in items["open"]}
    assert by["1.4.3"] == {"criterion": "1.4.3", "title": "Contrast (Minimum)", "status": "needs_review",
                           "measured": True, "automated": {"pass": 1}}
    assert by["1.2.2"]["status"] == "not_applicable" and by["1.2.2"]["automated"] == {"not_applicable": 1}
    # Which specialist owns which open criteria is state the lead can plan with; none has been tried yet.
    assert set(items["specialists"]) == set(SPECIALIST_FOR_DOMAIN.values())
    assert "1.2.2" in items["specialists"]["audit_media"]["open"] and not items["specialists"]["audit_media"]["tried"]
    lead_area = [i for i in items["open"] if REGISTRY[i["criterion"]].domain == "lead"]
    assert len(lead_area) + sum(len(s["open"]) for s in items["specialists"].values()) == 55

    add_manual_finding(audit, "1.4.3", "pass", "Measured contrast covers the page.", "details: 40 text nodes incl. hero text")
    add_manual_finding(audit, "1.2.2", "not_applicable", "No media.", "Screenshot and accessibility tree show no media.")
    left = {i["criterion"] for i in open_items(audit)["open"]}
    assert "1.4.3" not in left and "1.2.2" not in left and len(left) == 53
    by_status = {c.wcag_criterion: c.status for c in compile_report(audit).criteria}
    assert by_status["1.4.3"] is Status.PASS and by_status["1.2.2"] is Status.NOT_APPLICABLE
    assert by_status["2.4.2"] is Status.NEEDS_REVIEW  # an automated pass alone is still only needs_review


def test_specialist_leftovers_stay_open_for_the_lead():
    audit = swept()
    audit.mark_attempted("audit_forms")
    items = open_items(audit)
    forms = [i for i in items["open"] if REGISTRY[i["criterion"]].domain == "forms"]
    assert forms and items["specialists"]["audit_forms"]["tried"]  # tried, and what it left is still open
    assert not items["exhausted_for_human"]  # nothing is exhausted until an agent has judged it


def test_specialist_view_is_scoped_to_its_domain():
    audit = swept("AAA")
    items = open_items(audit, domain="media")
    assert {i["criterion"] for i in items["open"]} == {t.criterion for t in REGISTRY.values() if t.domain == "media"}
    assert "specialists" not in items
    add_manual_finding(audit, "1.2.2", "pass", "Captions present.", "CC control and track element.")
    assert open_items(audit, domain="media")["open_count"] == 14 and open_items(audit)["open_count"] == 85


def test_batch_judgements_record_each_criterion_and_report_bad_ones():
    audit = swept()
    result = add_manual_findings(audit, [
        Judgement(criterion="2.4.2", status="pass", description="Descriptive title.", evidence="Title 'Contact us - Shop'.", selectors=[]),
        Judgement(criterion="1.3.4", status="pass", description="Both orientations work.", evidence="Portrait/landscape text equal.", selectors=[]),
        Judgement(criterion="4.1.1", status="pass", description="Parses.", evidence="n/a", selectors=[]),
        Judgement(criterion="3.1.2", status="needs_review", description="Unsure.", evidence="", selectors=[]),
    ])
    assert [r["recorded"] for r in result["recorded"]] == ["2.4.2", "1.3.4"]
    assert len(result["errors"]) == 2 and "obsolete" in result["errors"][0]["error"] and "Evidence" in result["errors"][1]["error"]
    assert open_items(audit)["open_count"] == 53


def test_open_items_follow_the_target_level():
    assert open_items(fresh_audit("A"))["open_count"] == 31
    assert open_items(fresh_audit("AA"))["open_count"] == 55
    assert open_items(fresh_audit("AAA"))["open_count"] == 86


def test_tools_then_judgement_close_items():
    audit = fresh_audit()
    for tool in REGISTRY.values():
        audit.mark_attempted(tool.name)
    for specialist in SPECIALIST_FOR_DOMAIN.values():
        audit.mark_attempted(specialist)
    remaining = open_items(audit)
    assert all(i["measured"] for i in remaining["open"]) and all(s["tried"] for s in remaining["specialists"].values())
    # Any recorded judgement counts as reviewed, even needs_review: that is what sends a criterion to a human.
    for item in remaining["open"]:
        add_manual_finding(audit, item["criterion"], "needs_review", "Checked.", "Looked at the screenshot.")
    after = open_items(audit)
    assert after["done"] and len(after["exhausted_for_human"]) == 55


def test_settled_criteria_are_not_open():
    audit = fresh_audit()
    add_manual_finding(audit, "2.4.2", "pass", "Descriptive title.", "Title is 'Contact us - Shop'.")
    audit.add_findings([Finding(rule_id="WCAG_1_1_1", wcag_criterion="1.1.1", status=Status.FAIL, source="axe",
                                check_id="check_non_text_content", page_url="page", description="Missing alt")])
    open_criteria = {i["criterion"] for i in open_items(audit)["open"]}
    assert "2.4.2" not in open_criteria and "1.1.1" not in open_criteria


def test_not_applicable_aggregation():
    audit = fresh_audit()
    add_manual_finding(audit, "1.2.2", "not_applicable", "No video.", "No media found.")
    add_manual_finding(audit, "1.2.5", "not_applicable", "No video.", "No media found.")
    add_manual_finding(audit, "1.2.5", "fail", "Video lacks AD.", "A video was found later.")
    by = {c.wcag_criterion: c.status for c in compile_report(audit).criteria}
    assert by["1.2.2"] is Status.NOT_APPLICABLE
    assert by["1.2.5"] is Status.FAIL  # a failure always wins
    assert compile_report(audit).counts["not_applicable"] == 1


def test_report_covers_the_target_level():
    assert len(compile_report(fresh_audit("A")).criteria) == 31
    assert len(compile_report(fresh_audit("AAA")).criteria) == 86
