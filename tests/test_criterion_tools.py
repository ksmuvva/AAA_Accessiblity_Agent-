"""Criteria as rules + checks, reached by agents through generic tools (get_criterion to read, run_checks to measure)."""

import asyncio
from pathlib import Path

from agent_access.team.audit_agent import build_audit_agent
from agent_access.team.specialists import build_specialist
from agent_access.catalogue import sync as sync_catalogue
from agent_access.catalogue.catalogue import load_catalogue
from agent_access.agent_tools.browser_tools import load_html_content
from agent_access.agent_tools.check_tools import run_checks_now
from agent_access.context import AuditContext
from agent_access.engine import criterion_view, run_sweep
from agent_access.agent_tools.planning import open_items
from agent_access.rules import REGISTRY, TOOLS_IN_ORDER

FIXTURES = Path(__file__).parent / "fixtures"


def _enum(agent) -> list[str]:
    tool = next(t for t in agent.tools if t.name == "get_criterion")
    return tool.params_json_schema["properties"]["criterion"]["enum"]


def test_each_agent_gets_generic_tools_scoped_to_its_criteria():
    domains = {d: build_specialist(d, "m") for d in ("forms", "media", "navigation")}
    for domain, agent in domains.items():
        names = [t.name for t in agent.tools]
        assert {"get_criterion", "run_checks", "get_open_items", "record_findings"} <= set(names)
        assert "rerun_check" not in names and "run_all_checks" not in names  # one measuring tool; the sweep is the lead's
        assert not [n for n in names if n.startswith("check_")]
        assert _enum(agent) == [t.criterion for t in TOOLS_IN_ORDER if t.domain == domain]
        assert len(names) <= 13  # 12 plus evaluate_js, the agent's own read-only script
    lead = build_audit_agent("m")
    assert len(_enum(lead)) == 86  # the lead judges whatever specialists leave open
    assert {"run_checks", "run_all_checks"} <= {t.name for t in lead.tools}
    assert len(lead.tools) <= 19  # 18 plus evaluate_js


def test_catalogue_is_in_sync_with_the_rules():
    # --check proves the generated fields (implementation_status, automated_checks, ...) match the registry.
    assert sync_catalogue.main(["--check"]) == 0, "run python -m agent_access.catalogue.sync"
    catalogue = load_catalogue()
    for tool in TOOLS_IN_ORDER:  # every criterion says what an agent must judge or confirm
        assert catalogue.get(tool.criterion).manual_checks, f"{tool.criterion} has no manual_checks"


def test_get_criterion_serves_the_rule_its_evidence_and_what_to_judge():
    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            await load_html_content(audit, (FIXTURES / "bad_page.html").read_text(encoding="utf-8"))
            await run_sweep(audit)
            return criterion_view(audit, "1.1.1"), criterion_view(audit, "1.4.3"), audit

    alt, contrast, audit = asyncio.run(scenario())
    assert alt["status"] == "fail" and alt["check"] == "check_non_text_content"
    assert any(f["status"] == "fail" for f in alt["findings"]) and "note" not in alt
    assert alt["your_judgement_needed"] == " ".join(load_catalogue().get("1.1.1").manual_checks) and not alt["measured_only"]
    # A measured rule asks the agent to confirm the measurement covered the page.
    assert contrast["status"] == "fail" and contrast["measured_only"]
    assert contrast["your_judgement_needed"].startswith("This criterion is measured by the check")
    assert contrast["your_judgement_needed"].endswith(" ".join(load_catalogue().get("1.4.3").manual_checks))
    na = criterion_view(audit, "1.2.2")
    assert na["status"] == "not_applicable" and na["your_judgement_needed"].startswith("The check found nothing")
    assert REGISTRY["1.1.1"].name in audit.rule_details[audit.current_url]


def test_unchecked_page_state_points_to_run_checks():
    html = "<html lang='en'><head><title>Checkout - Shop</title></head><body><main><h1>Checkout</h1></main></body></html>"

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            await load_html_content(audit, html)  # loaded but not swept, like a newly labelled process state
            before = criterion_view(audit, "2.4.2"), open_items(audit)
            await run_checks_now(audit, ["2.4.2"])
            return before, criterion_view(audit, "2.4.2"), open_items(audit)

    (view, items), after, items_after = asyncio.run(scenario())
    assert "run_checks" in view["note"] and view["status"] == "not_tested"
    by, by_after = ({i["criterion"]: i for i in x["open"]} for x in (items, items_after))
    assert not by["2.4.2"]["measured"] and "2.4.2" in items["unmeasured"]  # unmeasured: measure it with run_checks
    assert "note" not in after and after["findings"]
    assert by_after["2.4.2"]["measured"] and "2.4.2" not in items_after["unmeasured"]
