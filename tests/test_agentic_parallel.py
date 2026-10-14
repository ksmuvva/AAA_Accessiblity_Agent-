"""G3 (parallel agents): a fork shares every result with the root context but owns a browser page, lock and cursor;
run_parallel / run_agents run jobs concurrently on forks with errors isolated; concurrent collect() calls run a probe once;
network_guard records tripwires. No OpenAI calls."""

import asyncio
import json

from agents import Agent, ModelResponse, Usage, RunContextWrapper, function_tool
from agents.models.interface import Model
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

from agent_access.agent_tools.browser_tools import click, load_html_content, page_outline
from agent_access.agent_tools.report_tools import add_manual_finding
from agent_access.context import AuditContext, run_agents, run_parallel
from agent_access.engine import run_rule
from agent_access.probes import PROBES, Probe, collect
from agent_access.rules import REGISTRY

KEY = "fork_test.html"
HTML = ("<html lang='en'><head><title>Fork test</title></head><body><h1>Hello</h1>"
        "<button id='b' onclick=\"document.body.classList.add('clicked')\">Go</button></body></html>")
FORM_HTML = ("<html lang='en'><head><title>Form</title></head><body>"
             "<form method='post' action='https://example.invalid/submit'><button type='submit'>Send</button></form>"
             "</body></html>")


async def _loaded(audit: AuditContext) -> None:
    await load_html_content(audit, HTML, KEY)


def test_fork_shares_results_but_owns_its_page():
    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            await _loaded(audit)
            fork = audit.fork("forms")
            assert fork.owner == "forms" and audit.owner is None and audit.can_fork()
            assert not fork.has_page() and audit.fork("forms") is fork and fork.fork("forms") is fork
            assert await fork.restore_page(KEY)
            assert fork.has_page() and fork.current_url == KEY
            root_page, fork_page = await audit.page(), await fork.page()
            assert root_page is not fork_page and await audit.fork("forms").page() is fork_page
            assert (await page_outline(fork))["title"] == "Fork test"

            # Shared results: a judgement recorded through the fork is visible on the root, and a reassignment too.
            add_manual_finding(fork, "2.4.2", "pass", "Title fine.", "Title 'Fork test'.")
            assert [f.wcag_criterion for f in audit.findings] == ["2.4.2"]
            fork.findings = []
            assert audit.findings == [] and fork.catalogue is audit.catalogue and fork.pages is audit.pages
            fork.mark_attempted("x")
            assert audit.attempted_on(KEY) == {"x"}

            # Own page state: clicking on the fork changes and dirties only the fork's page.
            await click(fork, "#b")
            assert KEY in fork.dirty and KEY not in audit.dirty
            assert await fork_page.evaluate("document.body.classList.contains('clicked')")
            assert not await root_page.evaluate("document.body.classList.contains('clicked')")
            await fork.close()
            assert not fork.has_page() and audit.has_page() and "forms" not in audit._forks

    asyncio.run(scenario())


def test_run_parallel_runs_jobs_concurrently_on_forks_and_isolates_errors():
    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            await _loaded(audit)
            active = peak = 0

            async def job(fork, criterion):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                assert fork.owner is not None and fork.current_url == KEY
                await asyncio.sleep(0.3)
                result = await run_rule(fork, REGISTRY[criterion])
                active -= 1
                return result["criterion"]

            async def broken(fork):
                raise ZeroDivisionError("boom")

            results = await run_parallel(audit, {"a": lambda f: job(f, "2.4.2"), "b": lambda f: job(f, "3.1.1"),
                                                 "c": broken})
            return audit, results, peak

    audit, results, peak = asyncio.run(scenario())
    assert peak >= 2, "jobs did not overlap"
    assert results["a"].startswith("2.4.2") and results["b"].startswith("3.1.1")
    assert isinstance(results["c"], ZeroDivisionError)
    # Everything landed in the shared state under the page key; the root's cursor and forks are cleaned up.
    assert {f.wcag_criterion for f in audit.findings} == {"2.4.2", "3.1.1"}
    assert {r["tool"] for r in audit.tool_runs} == {REGISTRY["2.4.2"].name, REGISTRY["3.1.1"].name}
    assert all(f.page_url == KEY for f in audit.findings)
    assert audit.current_url == KEY and not audit._forks


def test_run_parallel_falls_back_to_sequential_with_real_nvda():
    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            await _loaded(audit)
            audit.nvda = object()  # real NVDA reads the foreground window: one page at a time
            assert not audit.can_fork()
            seen = []

            async def job(ctx, name):
                seen.append((name, ctx.owner, ctx is audit))
                return name

            results = await run_parallel(audit, {"x": lambda c: job(c, "x"), "y": lambda c: job(c, "y")})
            audit.nvda = None
            return results, seen

    results, seen = asyncio.run(scenario())
    assert results == {"x": "x", "y": "y"} and seen == [("x", None, True), ("y", None, True)]


def test_concurrent_collect_runs_a_probe_once():
    runs = 0

    async def slow_probe(audit):
        nonlocal runs
        runs += 1
        await asyncio.sleep(0.2)
        return {"n": runs}

    PROBES["_slow"] = Probe("_slow", slow_probe, False)
    try:
        async def scenario():
            async with AuditContext(screen_reader="emulated") as audit:
                await _loaded(audit)
                forks = [audit.fork(n) for n in ("a", "b")]
                for f in forks:
                    assert await f.restore_page(KEY)
                return await asyncio.gather(collect(audit, "_slow"), collect(forks[0], "_slow"), collect(forks[1], "_slow"))

        results = asyncio.run(scenario())
    finally:
        del PROBES["_slow"]
    assert runs == 1 and all(r.data == {"n": 1} for r in results)


TWO_FORMS = ("<html lang='en'><head><title>Forms</title></head><body>"
             "<form id='f1' action='/a' method='post'><label>Name <input name='n' required></label>"
             "<button type='submit'>Send 1</button></form>"
             "<form id='f2' action='/b' method='post'><label>Email <input name='e' type='email' required></label>"
             "<button type='submit'>Send 2</button></form></body></html>")


def test_concurrent_retests_on_forks_merge_into_one_probe_cache():
    """Two forks re-test different forms at once: both merges land in the shared cached data (b7's review item)."""
    from agent_access.engine import retest

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            await load_html_content(audit, TWO_FORMS, "forms.html")
            await collect(audit, "forms")
            results = await run_parallel(audit, {"a": lambda f: retest(f, "forms", ["#f1"]),
                                                 "b": lambda f: retest(f, "forms", ["#f2"])})
            return audit, results

    audit, results = asyncio.run(scenario())
    assert all("error" not in r for r in results.values()), results
    assert all(r["measured"].get("forms") for r in results.values())
    selectors = {f.get("form_selector") or f.get("form") for f in audit.observations["forms.html"]["forms"].data["forms"]}
    assert len(selectors) >= 2  # neither fork's merge overwrote the other's
    assert {r["tool"] for r in audit.tool_runs if r["page_url"] == "forms.html"} >= {REGISTRY["3.3.1"].name}


def test_network_guard_records_tripwires():
    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            await load_html_content(audit, FORM_HTML, "form.html")
            page = await audit.page()
            async with audit.network_guard() as blocked:  # a probe's guard
                await page.click("button[type=submit]")
                await page.wait_for_timeout(500)
            async with audit.network_guard(source="agent", tool="evaluate_js") as blocked2:  # an agent's own action
                await page.click("button[type=submit]")
                await page.wait_for_timeout(500)
            return blocked, blocked2, list(audit.tripwires), await page.title()

    blocked, blocked2, tripwires, title = asyncio.run(scenario())
    assert blocked and blocked2 and len(tripwires) == 2 and title == "Form"  # blocked, and the page stayed
    probe, agent = tripwires
    assert probe["kind"] == "blocked_navigation" and probe["detail"].startswith("POST https://example.invalid/submit")
    assert probe["page_url"] == "form.html" and probe["owner"] is None and probe["time"] and probe["action"] is None
    # The same block of something an agent did is a reportable safety event (G5's tripwire_report shows it).
    assert agent["kind"] == "write_blocked" and agent["action"] == "blocked" and agent["tool"] == "evaluate_js"


class ScriptModel(Model):
    """A fake model: a tool call, then a final text message."""

    def __init__(self, tool_name: str):
        self.steps = [tool_name, None]
        self.n = 0

    async def get_response(self, system_instructions, input, model_settings, tools, output_schema, handoffs,
                           tracing, *, previous_response_id=None, conversation_id=None, prompt=None):
        self.n += 1
        step = self.steps.pop(0)
        if step:
            item = ResponseFunctionToolCall(id=f"fc_{self.n}", call_id=f"call_{self.n}", type="function_call",
                                            name=step, arguments=json.dumps({}), status="completed")
        else:
            item = ResponseOutputMessage(id=f"msg_{self.n}", type="message", role="assistant", status="completed",
                                         content=[ResponseOutputText(type="output_text", text="done", annotations=[])])
        return ModelResponse(output=[item], usage=Usage(), response_id=None)

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError


def test_run_agents_runs_sdk_agents_on_their_own_forks():
    owners = []

    @function_tool
    async def note_owner(ctx: RunContextWrapper[AuditContext]) -> str:
        """Record which context this tool ran on."""
        owners.append((ctx.context.owner, ctx.context.current_url, ctx.context.has_page()))
        return "noted"

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            await _loaded(audit)
            agents = [Agent[AuditContext](name=name, instructions="You are a test agent.", tools=[note_owner],
                                          model=ScriptModel("note_owner")) for name in ("Forms specialist", "Media specialist")]
            results = await run_agents(audit, [(agents[0], "go"), (agents[1], "go")], max_turns=3)
            return results

    results = asyncio.run(scenario())
    assert [r.final_output for r in results] == ["done", "done"]
    assert sorted(owners) == [("Forms specialist", KEY, True), ("Media specialist", KEY, True)]
