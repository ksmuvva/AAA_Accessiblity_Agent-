"""The automated sweep: every engine and all 86 criterion tools run on every page, and every run is logged."""

import asyncio
import json
from pathlib import Path

import pytest
from agents.tool_context import ToolContext

from agent_access.agent_tools.browser_tools import label_page_state, load_html_content
from agent_access.context import AuditContext
from agent_access.probes import PROBE_ORDER
from agent_access.engine import SITE_CRITERIA, run_site_tests, run_sweep
from agent_access.rules import TOOLS_IN_ORDER

FIXTURES = Path(__file__).parent / "fixtures"


def sweep_fixtures(*names: str, level: str = "AA") -> AuditContext:
    async def scenario():
        async with AuditContext(target_level=level, screen_reader="emulated") as audit:
            for name in names:
                await load_html_content(audit, (FIXTURES / name).read_text(encoding="utf-8"), name)
                await run_sweep(audit)
            await run_site_tests(audit)
            return audit

    return asyncio.run(scenario())


@pytest.fixture(scope="module")
def form_audit():
    return sweep_fixtures("form_page.html")


def test_all_86_tools_run_without_errors(form_audit):
    runs = [r for r in form_audit.tool_runs if r["page_url"] == "form_page.html"]
    assert [r["tool"] for r in runs] == [t.name for t in TOOLS_IN_ORDER]
    assert len(runs) == 86
    assert all(r["ok"] for r in runs), [(r["tool"], r["error"]) for r in runs if not r["ok"]]
    # Every tool records at least one finding, so nothing is silently skipped.
    assert all(r["findings"] for r in runs), [r["tool"] for r in runs if not r["findings"]]
    assert {(r["criteria"], r["level"]) for r in runs} == {(t.criterion, t.level) for t in TOOLS_IN_ORDER}


def test_findings_are_recorded_under_each_tool_name(form_audit):
    by_tool = {}
    for f in form_audit.findings:
        by_tool.setdefault(f.check_id, set()).add(f.wcag_criterion)
    for tool in TOOLS_IN_ORDER:
        assert by_tool.get(tool.name) == {tool.criterion}, tool.name


def test_probes_run_once_per_page(form_audit):
    assert set(form_audit.observations["form_page.html"]) == set(PROBE_ORDER)
    assert form_audit.engines["axe-core"].startswith("axe-core 4.")
    assert "emulated" in form_audit.engines["screen reader"].lower() or "accessibility tree" in form_audit.engines["screen reader"]


def test_labelling_a_page_state_through_the_tool_runs_the_sweep():
    async def scenario():
        async with AuditContext(screen_reader="emulated", allow_interaction=True) as audit:
            await load_html_content(audit, (FIXTURES / "good_page.html").read_text(encoding="utf-8"))
            args = json.dumps({"label": "menu-open"})
            ctx = ToolContext(audit, tool_name="label_page_state", tool_call_id="call_1", tool_arguments=args)
            out = json.loads(await label_page_state.on_invoke_tool(ctx, args))
            return audit, out

    audit, out = asyncio.run(scenario())
    assert out["automated_sweep"]["criterion_tools_run"] == 86
    assert audit.swept == {"about:inline-html [menu-open]"}


def test_site_criteria_rerun_across_pages():
    audit = sweep_fixtures("good_page.html", "form_page.html")
    for page in ("good_page.html", "form_page.html"):
        reruns = [r for r in audit.tool_runs if r["page_url"] == page and r["criteria"] in SITE_CRITERIA]
        assert len(reruns) == 2 * len(SITE_CRITERIA)  # once in the sweep, once after all pages
    statuses = {f.status.value for f in audit.findings if f.wcag_criterion == "3.2.3"}
    assert statuses <= {"pass", "fail", "needs_review"}
