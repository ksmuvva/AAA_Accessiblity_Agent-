"""Lane G6: agentic measurement. Lazy mode lets the agents choose what to measure; the code floor still measures
every target-level criterion before a page is done; eager mode is today's behaviour; open_items keeps its contract and
shows what is unmeasured. No model calls."""

import asyncio
from pathlib import Path

from agent_access.agent_tools.browser_tools import load_html_content
from agent_access.agent_tools.check_tools import (
    CHEAP_PROBES, check_tools_for, measure_on_load, run_checks_now, run_sweep_remaining,
)
from agent_access.agent_tools.planning import open_items, unmeasured_criteria
from agent_access.agent_tools.report_tools import add_manual_finding
from agent_access.context import AuditContext
from agent_access.rules import REGISTRY, TOOLS_IN_ORDER

FIXTURES = Path(__file__).parent / "fixtures"
PAGE = "file:good_page.html"


def lazy_audit() -> AuditContext:
    audit = AuditContext(screen_reader="emulated")
    audit.measurement = "lazy"  # the field lands in AuditContext with lane G3; until then it is a plain attribute
    return audit


async def load(audit: AuditContext) -> None:
    await load_html_content(audit, (FIXTURES / "good_page.html").read_text(encoding="utf-8"), PAGE)


def test_lazy_load_measures_nothing_but_shows_everything_as_unmeasured():
    async def scenario():
        async with lazy_audit() as audit:
            await load(audit)
            runs = await measure_on_load(audit)
            return runs, set(audit.observations[PAGE]), open_items(audit), len(audit.tool_runs), PAGE in audit.swept

    runs, probes, items, tool_runs, swept = asyncio.run(scenario())
    assert runs == [] and tool_runs == 0 and not swept
    assert probes == set(CHEAP_PROBES)  # only the cheap read-only probes were collected
    assert items["measurement"] == "lazy" and items["open_count"] == 55 and len(items["unmeasured"]) == 55
    assert set(items["tests_to_run"]) == {"run_checks"} and len(items["tests_to_run"]["run_checks"]) == 55
    assert all(i["status"] == "not_tested" and i["next_tests"] == ["run_checks"] for i in items["open"])


def test_run_checks_measures_only_what_the_rules_need():
    async def scenario():
        async with lazy_audit() as audit:
            await load(audit)
            await measure_on_load(audit)
            first = await run_checks_now(audit, ["2.4.2", "3.1.1", "9.9.9"])
            probes_after_first = set(audit.observations[PAGE])
            second = await run_checks_now(audit, ["1.4.3"])
            probes_after_second = set(audit.observations[PAGE])
            checks = {f.check_id for f in audit.findings_for(PAGE)}
            return first, probes_after_first, second, probes_after_second, checks, open_items(audit)

    first, probes1, second, probes2, checks, items = asyncio.run(scenario())
    assert [r["criterion"][:5] for r in first["ran"]] == ["2.4.2", "3.1.1"] and first["ignored"] == ["9.9.9"]
    assert probes1 == set(CHEAP_PROBES)  # title and language rules read probes the lazy load already had
    assert "contrast" in probes2 and probes2 - probes1 <= {"contrast"}  # 1.4.3 collected exactly what it needed
    assert checks == {"check_page_title", "check_page_language", "check_minimum_text_contrast"}
    assert all(r["your_judgement_needed"] for r in first["ran"] + second["ran"])
    assert first["unmeasured_after"] == 53 and second["unmeasured_after"] == 52 and len(items["unmeasured"]) == 52
    # Measured criteria stay open until an agent judges them: the coverage contract is unchanged.
    assert items["open_count"] == 55 and "2.4.2" in items["tests_to_run"]["review"]
    assert sorted(items["tests_to_run"]["run_checks"]) == sorted(items["unmeasured"])


def test_the_floor_measures_every_target_criterion_the_agent_did_not():
    async def scenario():
        async with lazy_audit() as audit:
            await load(audit)
            await measure_on_load(audit)
            await run_checks_now(audit, ["2.4.2", "3.1.1"])
            floor = await run_sweep_remaining(audit)
            again = await run_sweep_remaining(audit)
            target = {REGISTRY[r.wcag_criterion].name for r in audit.target_rules()}
            return (floor, again, open_items(audit), unmeasured_criteria(audit), audit.attempted_on(PAGE),
                    PAGE in audit.swept, target)

    floor, again, items, unmeasured, tried, swept, target = asyncio.run(scenario())
    assert len(floor) == 53 and again == []  # the 55 AA-target rules minus the two the agent ran; idempotent
    assert {r["tool"] for r in floor} == target - {"check_page_title", "check_page_language"}
    assert unmeasured == [] and items["unmeasured"] == [] and swept
    assert tried >= target
    assert items["open_count"] > 0 and "run_checks" not in items["tests_to_run"]  # measured, still to be judged


def test_the_floor_leaves_criteria_above_the_target_to_the_agents():
    async def scenario():
        async with lazy_audit() as audit:
            await load(audit)
            await measure_on_load(audit)
            await run_checks_now(audit, ["1.4.6"])  # an AAA criterion the agent chose to measure
            await run_sweep_remaining(audit)
            return audit.attempted_on(PAGE), {REGISTRY[r.wcag_criterion].name for r in audit.beyond_target_rules()}

    tried, beyond = asyncio.run(scenario())
    assert tried & beyond == {REGISTRY["1.4.6"].name}  # only the one the agent asked for; the floor ran no AAA rule


def test_the_report_says_which_criteria_above_the_target_were_not_measured():
    from agent_access.agent_tools.report_tools import compile_site_report
    from agent_access.report_writer import render_markdown
    from agent_access.team.orchestrator import AuditOutcome

    async def scenario():
        async with lazy_audit() as audit:
            await load(audit)
            await measure_on_load(audit)
            await run_checks_now(audit, ["1.4.6"])
            await run_sweep_remaining(audit)
            return render_markdown(AuditOutcome(report=compile_site_report(audit))), len(audit.beyond_target_rules())

    md, beyond = asyncio.run(scenario())
    assert f"1 higher-level criteria were also tested; {beyond - 1} were not measured" in md


def test_the_floor_measures_everything_when_the_target_is_aaa():
    async def scenario():
        async with lazy_audit() as audit:
            audit.target_level = "AAA"
            await load(audit)
            await measure_on_load(audit)
            return await run_sweep_remaining(audit)

    assert len(asyncio.run(scenario())) == len(TOOLS_IN_ORDER)


A_PAGE = "<html lang='en'><head><title>Alpha</title></head><body><main><h1>Alpha</h1><a href='/alpha'>Alpha link</a></main></body></html>"
B_PAGE = "<html><head></head><body><p>Beta has no title, no lang, no links.</p></body></html>"


def test_the_floor_measures_the_page_it_records_not_the_page_the_browser_shows():
    """Cross-lane review R11: the floor for page A must run on A's DOM even if the browser is on B (lazy mode
    leaves pages unmeasured, and the planner runs the floor over every page at the end)."""
    async def scenario():
        async with lazy_audit() as audit:
            await load_html_content(audit, A_PAGE, "file:a.html")
            await measure_on_load(audit)
            await load_html_content(audit, B_PAGE, "file:b.html")
            await measure_on_load(audit)
            assert audit.current_url == "file:b.html"
            with audit.at_page("file:a.html"):  # how the planner and orchestrator call it: cursor moved, browser not
                ran = await run_sweep_remaining(audit)
            a = audit.findings_for("file:a.html")
            links_a = audit.observations["file:a.html"]["page"].data.get("links", [])
            title_now = await (await audit.page()).title()
            skipped = await run_sweep_remaining(audit, "file:a.html [menu-open]")
            return len(ran), a, links_a, title_now, "file:b.html" in audit.swept, skipped, audit.warnings

    ran, a, links_a, title_now, b_swept, skipped, warnings = asyncio.run(scenario())
    title = [f for f in a if f.check_id == "check_page_title"]
    lang = [f for f in a if f.check_id == "check_page_language"]
    # On B's DOM both rules fail (no <title>, no lang); on A's they pass axe and ask the agent to judge "Alpha".
    assert ran == 55 and not [f for f in title + lang if f.status.value == "fail"]  # every AA-target rule
    assert any("Alpha" in f.description for f in title)
    assert len(links_a) == 1 and links_a[0]["text"] == "Alpha link"  # A's probes describe A, not B
    assert title_now == "Alpha" and not b_swept  # the floor reloaded A; B was left alone
    assert skipped == [] and any("can't be re-created" in w for w in warnings)  # a state can't be reloaded: say so


def test_eager_mode_is_todays_sweep():
    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:  # no measurement attribute: eager by default
            await load(audit)
            runs = await measure_on_load(audit)
            return runs, open_items(audit), PAGE in audit.swept, await run_sweep_remaining(audit)

    runs, items, swept, floor = asyncio.run(scenario())
    assert len(runs) == 86 and swept and floor == []
    assert items["measurement"] == "eager" and items["unmeasured"] == [] and "run_checks" not in items["tests_to_run"]


def test_judging_a_measured_criterion_closes_it_in_lazy_mode_too():
    async def scenario():
        async with lazy_audit() as audit:
            await load(audit)
            await measure_on_load(audit)
            await run_checks_now(audit, ["2.4.2"])
            add_manual_finding(audit, "2.4.2", "pass", "Descriptive title.", "Title names the page.")
            return open_items(audit)

    items = asyncio.run(scenario())
    assert "2.4.2" not in {i["criterion"] for i in items["open"]} and "2.4.2" not in items["unmeasured"]
    assert items["open_count"] == 54 and len(items["unmeasured"]) == 54


def test_check_tools_are_scoped_like_the_criterion_tools():
    lead = {t.name: t for t in check_tools_for(None)}
    assert set(lead) == {"run_checks", "run_all_checks"}
    schema = lead["run_checks"].params_json_schema["properties"]["criteria"]
    assert schema["type"] == "array" and schema["items"]["enum"] == [t.criterion for t in TOOLS_IN_ORDER]
    forms = {t.name: t for t in check_tools_for("forms")}
    assert set(forms) == {"run_checks"}
    enum = forms["run_checks"].params_json_schema["properties"]["criteria"]["items"]["enum"]
    assert enum == [t.criterion for t in TOOLS_IN_ORDER if t.domain == "forms"] and len(enum) == 14
    assert all(REGISTRY[c].domain == "forms" for c in enum)
