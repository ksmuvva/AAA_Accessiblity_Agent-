"""The planning loop's bookkeeping: what is open, what is exhausted, and how status aggregation treats it.

The coverage contract: on every page, every criterion at the target level stays open until it fails or an agent has
recorded a judgement for it, whatever its automated check said (needs review, passed, or not applicable).
"""

from agent_access.context import AuditContext
from agent_access.models import Finding, Judgement, Status
from agent_access.agent_tools.planning import SPECIALIST_FOR_DOMAIN, open_items
from agent_access.agent_tools.report_tools import (STATE_CRITERIA, add_manual_finding, add_manual_findings,
                                                   compile_report, compile_site_report)
from agent_access.probes import ProbeResult
from agent_access.rules import REGISTRY
from agent_access.team.reviewer import needs_review_check

NOTHING_REVEALED = ProbeResult({"results": []})  # the disclosures probe ran: no control revealed content


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
    audit.observations["page"] = {"disclosures": NOTHING_REVEALED}
    return audit


def fresh_audit(level: str = "AA") -> AuditContext:
    audit = AuditContext(target_level=level)
    audit.set_current("page")
    audit.observations["page"] = {"disclosures": NOTHING_REVEALED}
    return audit


def test_not_applicable_waits_until_the_controls_that_reveal_content_are_opened():
    """The checks saw the page as loaded: a form built when a button is pressed was never measured (the eval's
    flow_explore false passes). An agent's not-applicable needs that content opened in a labelled state first."""
    audit = AuditContext()
    audit.set_current("page")
    unmeasured = add_manual_finding(audit, "3.3.2", "not_applicable", "No form.", "list_forms returned [].")
    assert 'run_checks(["2.1.1"])' in unmeasured["error"]
    for failed in (ProbeResult({}, "TimeoutError: page.evaluate"), ProbeResult({"results": [], "stopped_early": "reload failed"})):
        audit.observations["page"] = {"disclosures": failed}   # a failed or partial measurement measured nothing
        assert "couldn't be fully measured" in add_manual_finding(audit, "3.3.2", "not_applicable", "No form.", "None.")["error"]
    audit.observations["page"] = {"disclosures": ProbeResult({"results": [
        {"trigger": "#open-settings", "name": "Open settings", "opened_by": "Enter", "content": "Settings Language Save",
         "aria_expanded": {"before": "false", "after": "true"}, "is_dialog": False},
        {"trigger": "#alerts", "name": "Off", "opened_by": None, "content": None}]})}
    refused = add_manual_finding(audit, "3.3.2", "not_applicable", "No form.", "list_forms returned [].")
    assert "'Open settings' (#open-settings) shows 'Settings Language Save'" in refused["error"]
    assert "#alerts" not in refused["error"] and not audit.findings
    assert add_manual_finding(audit, "3.3.2", "needs_review", "Form behind a button.", "It reveals a form.")["recorded"]
    audit.set_current("page [settings]")   # the agent opened the panel and labelled that state
    audit.set_current("page")
    assert add_manual_finding(audit, "1.2.2", "not_applicable", "No media.", "No video or audio, opened or not.")["recorded"]


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
    for f in audit.findings:
        f.review_verdict = "confirmed"   # the reviewer agreed: only now do the verdicts reach the report
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


def test_an_unconfirmed_automated_not_applicable_is_reported_as_needs_review():
    """The check saw the page as loaded: content behind a button would be missed. Only an auditor's not-applicable is
    reported as such; the agents' own view still shows what the check found."""
    audit = fresh_audit()
    audit.replace_findings("page", None, [automated("1.2.2", Status.NOT_APPLICABLE)], "check_prerecorded_captions")
    reported = next(c for c in compile_report(audit).criteria if c.wcag_criterion == "1.2.2")
    assert reported.status is Status.NEEDS_REVIEW and "no auditor confirmed it" in reported.summary
    assert next(i for i in open_items(audit)["open"] if i["criterion"] == "1.2.2")["status"] == "not_applicable"
    add_manual_finding(audit, "1.2.2", "not_applicable", "No media.", "No video, audio or embeds in the page or tree.")
    audit.findings[-1].review_verdict = "confirmed"
    assert next(c for c in compile_report(audit).criteria if c.wcag_criterion == "1.2.2").status is Status.NOT_APPLICABLE


def test_not_applicable_aggregation():
    audit = fresh_audit()
    add_manual_finding(audit, "1.2.2", "not_applicable", "No video.", "No media found.")
    add_manual_finding(audit, "1.2.5", "not_applicable", "No video.", "No media found.")
    add_manual_finding(audit, "1.2.5", "fail", "Video lacks AD.", "A video was found later.")
    for f in audit.findings:
        f.review_verdict = "confirmed"
    by = {c.wcag_criterion: c.status for c in compile_report(audit).criteria}
    assert by["1.2.2"] is Status.NOT_APPLICABLE
    assert by["1.2.5"] is Status.FAIL  # a failure always wins
    assert compile_report(audit).counts["not_applicable"] == 1


def test_report_covers_the_target_level():
    assert len(compile_report(fresh_audit("A")).criteria) == 31
    assert len(compile_report(fresh_audit("AAA")).criteria) == 86


STATE = "page [error shown]"


def with_state(changed: str = "1.4.3") -> AuditContext:
    """The swept page and a state labelled on it whose checks all found the same, except `changed` (new text)."""
    audit = swept()
    audit.set_current(STATE)
    for tool in REGISTRY.values():
        audit.mark_attempted(tool.name)
        f = automated(tool.criterion, Status.PASS).model_copy(update={"page_url": STATE})
        audit.add_findings([f.model_copy(update={"description": "12 text elements"}) if tool.criterion == changed else f])
    return audit


def test_a_state_owes_only_what_it_changed_and_what_only_a_state_shows():
    """booking_2's two explored states each owed all 55 criteria again and spent the budget; most were unchanged."""
    audit = with_state()
    items = open_items(audit, STATE)
    assert {i["criterion"] for i in items["open"]} == {"1.4.3", *STATE_CRITERIA}
    assert items["inherited"] == len(audit.target_rules()) - 1 - len(STATE_CRITERIA)
    assert open_items(audit, "page")["open_count"] == len(audit.target_rules())   # the page itself still owes everything
    add_manual_finding(audit, "2.4.2", "fail", "The title didn't change.", "Title 'Book' over an error.")
    after = open_items(audit, STATE)   # an agent's judgement in the state makes it the state's own: no longer inherited
    assert after["inherited"] == items["inherited"] - 1 and "2.4.2" not in {i["criterion"] for i in after["open"]}
    own = next(c for c in compile_report(audit, STATE).criteria if c.wcag_criterion == "2.4.2")
    assert own.inherited_from is None and any(f.evidence == "Title 'Book' over an error." for f in own.findings)


def test_an_inherited_criterion_reports_the_pages_result_once():
    audit = with_state()
    with audit.at_page("page"):
        add_manual_finding(audit, "1.1.1", "pass", "Alt text is meaningful.", "The logo's alt is 'Northline Rail'.")
    audit.findings[-1].review_verdict = "confirmed"
    state = next(c for c in compile_report(audit, STATE).criteria if c.wcag_criterion == "1.1.1")
    assert state.status is Status.PASS and state.inherited_from == "page" and not state.findings
    site = {c.wcag_criterion: c for c in compile_site_report(audit).criteria}
    assert site["1.1.1"].status is Status.PASS and site["1.1.1"].summary == "Passed: an auditor's judgement, confirmed by the reviewer."
    assert site["1.4.3"].summary == "needs_review on 2 of 2 page(s)."   # the state changed it: both count


def test_a_states_measured_failure_its_page_also_has_is_reviewed_once():
    audit = with_state()
    fails = []
    for key in ("page", STATE):
        fails.append(automated("1.1.1", Status.FAIL).model_copy(update={"page_url": key, "description": "Missing alt"}))
        audit.replace_findings(key, None, [fails[-1]], "check_non_text_content")
    assert needs_review_check(audit, fails[0]) and not needs_review_check(audit, fails[1])
    fails[0].original_status, fails[0].status = Status.FAIL, Status.NEEDS_REVIEW   # the reviewer doubted it on the page
    assert not needs_review_check(audit, fails[1])   # still the same measurement: the page's review stands for both


def test_a_state_owes_a_judgement_whose_quoted_content_changed_but_not_a_measurement_whose_timing_did():
    """Opening a panel adds headings that 2.4.6 quotes only in its evidence; 2.3.1's evidence is a frame rate."""
    audit = with_state()
    for f in audit.findings_for(STATE):
        if f.wcag_criterion == "2.4.6":
            f.evidence = "Headings: Account settings | Alerts"
        elif f.wcag_criterion == "2.3.1":
            f.evidence = "38 frames at 18.5 fps"
    owed = {i["criterion"] for i in open_items(audit, STATE)["open"]}
    assert "2.4.6" in owed and "2.3.1" not in owed
