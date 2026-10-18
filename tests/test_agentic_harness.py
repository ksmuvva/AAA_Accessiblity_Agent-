"""The agent-mode harness keeps the agents working: the lead is re-run on a page while criteria stay open (the same rule
as code mode), finish waits for labelled states the lead can rebuild, those states are rebuilt by replaying the
explorer's steps (for the lead and the reviewer), a browser error page is never labelled as a state, and the evals run
with interaction allowed, as the CLI does. No model calls."""

import asyncio
from argparse import Namespace

from test_agent import SUMMARY, RouterModel, call, confirm_all

from agent_access.agent_tools.browser_tools import click, label_state, load_html_content
from agent_access.context import AuditContext
from agent_access.team.planner import run_agentic_audit

PAGE = "file:ferry.html"
HTML = """<!doctype html><html lang="en"><head><title>Ferries - timetable</title></head><body><main>
<h1>Ferries</h1><p>Ferries leave every 20 minutes.</p>
<button id="open" aria-expanded="false">Open settings</button><section id="panel" hidden></section>
<a id="broken" href="http://127.0.0.1:9/nowhere">Old timetable</a>
</main><script>
document.getElementById('open').addEventListener('click', (ev) => {
  const panel = document.getElementById('panel');
  const heading = document.createElement('h2');
  heading.textContent = 'Settings panel';
  const label = document.createElement('label');
  label.htmlFor = 'mail';
  label.textContent = 'Email';
  const input = document.createElement('input');
  input.id = 'mail';
  input.type = 'email';
  panel.append(heading, label, input);
  panel.hidden = false;
  ev.currentTarget.setAttribute('aria-expanded', 'true');
});
</script></body></html>"""
STATE = PAGE + " [settings-open]"
FINAL = {"notes_for_human": []}
PLAN_DONE = {"pages_audited": [PAGE], "goal_changes": [], "stopped_because": "done", "notes_for_human": []}


def judge(criterion, status="pass"):
    return call("record_finding", {"criterion": criterion, "status": status, "description": f"Judged {criterion}.",
                                   "evidence": "Seen in the page outline and the accessibility tree.", "selectors": ["main"]})


def run(scripts, tmp_path, *, labelled_state=False, interaction=False, level="AA"):
    model = RouterModel(scripts)

    async def scenario():
        async with AuditContext(output_dir=tmp_path, screen_reader="emulated", tracing=False,
                                allow_interaction=interaction, target_level=level) as audit:
            await load_html_content(audit, HTML, PAGE)
            from agent_access.engine import run_sweep
            await run_sweep(audit)
            if labelled_state:   # as the explorer does: a click, then label_page_state; then back to the base page
                await click(audit, "#open")
                await label_state(audit, "settings-open")
                await audit.restore_page(PAGE)
            outcome = await run_agentic_audit(audit, model=model, progress=lambda m: None)
            return audit, outcome

    audit, outcome = asyncio.run(scenario())
    return model, audit, outcome


def tool_outputs(model, role, turn):
    return [i["output"] for i in model.inputs[role][turn] if i.get("type") == "function_call_output"]


def test_the_lead_is_worked_on_a_page_until_a_run_settles_nothing_more(tmp_path):
    scripts = {
        "audit planner": [call("audit_pages", {"page_keys": [PAGE], "focus": ""}), call("finish", {"reason": "done"}),
                          PLAN_DONE],
        "lead auditor": [judge("2.4.2"), FINAL,      # run 1 settles one criterion
                         judge("3.1.1"), FINAL,      # run 2, the continuation that always follows, settles another
                         FINAL],                     # run 3 settles nothing: the harness stops there
        "reviewer": [confirm_all],
        "summary writer": [SUMMARY],
    }
    model, audit, outcome = run(scripts, tmp_path)
    assert not model.scripts["lead auditor"]                       # exactly three lead runs, none left unused
    first, second, third = (model.inputs["lead auditor"][i][0]["content"] for i in (0, 2, 4))
    assert first == f"Audit this page: {PAGE}"
    assert second.startswith(f"Continue auditing {PAGE}.") and "2.4.2" not in second and "3.1.1 " in second
    assert "3.1.1 " not in third                                   # each continuation lists only what is still open
    audited = next(e for e in outcome.plan_log if e["action"] == "audit_page")
    assert audited["lead_runs"] == 3
    assert '"ok": true' in tool_outputs(model, "audit planner", 2)[-1]   # worked to a stop: finish no longer waits


def test_finish_waits_for_a_labelled_state_which_is_rebuilt_for_the_lead_and_the_reviewer(tmp_path):
    scripts = {
        "audit planner": [call("finish", {"reason": "done"}),            # refused: the page and its state are owed
                          call("audit_pages", {"page_keys": [STATE], "focus": ""}),
                          call("audit_pages", {"page_keys": [PAGE], "focus": ""}),
                          call("finish", {"reason": "done"}), PLAN_DONE],
        "lead auditor": [call("get_page_outline", {}), judge("3.3.2"), FINAL, FINAL,   # the state: rebuilt, then judged
                         FINAL, FINAL],                                  # the base page
        "reviewer": [confirm_all] * 2,
        "summary writer": [SUMMARY],
    }
    model, audit, outcome = run(scripts, tmp_path, labelled_state=True)
    refused = tool_outputs(model, "audit planner", 1)[-1]
    assert '"ok": false' in refused and STATE in refused and f'"{PAGE}"' in refused
    assert audit.state_paths[STATE]["steps"] == [{"do": "click", "selector": "#open"}]
    outline = tool_outputs(model, "lead auditor", 1)[-1]
    assert "Settings panel" in outline                             # the lead saw the rebuilt state, not the base page
    judged = next(f for f in audit.findings if f.page_url == STATE and f.source == "agent")
    assert judged.review_verdict == "confirmed"                     # the reviewer rebuilt the state too
    assert '"ok": true' in tool_outputs(model, "audit planner", 4)[-1]
    assert not model.scripts["lead auditor"] and outcome.errors == []


def test_a_planner_that_stops_early_is_handed_back_what_it_owes(tmp_path):
    scripts = {
        "audit planner": [call("open_page", {"url": PAGE}),      # the start page: already open, not an error
                          {**PLAN_DONE, "stopped_because": "blocked: the start page can't be opened"},
                          call("audit_pages", {"page_keys": [PAGE], "focus": ""}), call("finish", {"reason": "done"}),
                          PLAN_DONE],
        "lead auditor": [FINAL, FINAL],
        "reviewer": [confirm_all],
        "summary writer": [SUMMARY],
    }
    model, audit, outcome = run(scripts, tmp_path)
    assert '"already_open": true' in tool_outputs(model, "audit planner", 1)[-1]
    resumed = model.inputs["audit planner"][2][-1]["content"]
    assert resumed.startswith("You stopped without finish succeeding") and PAGE in resumed
    actions = [e["action"] for e in outcome.plan_log]
    assert actions.count("planner_resumed") == 1 and "finish" in actions and "lead_floor" not in actions
    assert not model.scripts["audit planner"] and not model.scripts["lead auditor"]


def test_finish_waits_for_a_flow_in_the_goal_to_be_explored(tmp_path):
    report = {"states": [{"label": "settings-open", "how_reached": "clicked Open settings", "what_changed": "a form"}],
              "flows": [], "links_worth_auditing": [], "blocked": []}
    scripts = {
        "audit planner": [call("set_goal", {"pages": 1, "level": "AA", "flows": ["open the settings"], "reason": "a form"}),
                          call("audit_pages", {"page_keys": [PAGE], "focus": ""}),
                          call("finish", {"reason": "done"}),             # refused: the flow was never explored
                          call("explore", {"page_key": PAGE, "goal": "open the settings"}),
                          call("audit_pages", {"page_keys": [STATE], "focus": ""}),
                          call("finish", {"reason": "done"}), PLAN_DONE],
        "lead auditor": [FINAL, FINAL, judge("3.3.2"), FINAL, FINAL],
        "explorer": [call("click_element", {"selector": "#open"}), call("label_page_state", {"label": "settings-open"}),
                     report],
        "reviewer": [confirm_all] * 2,
        "summary writer": [SUMMARY],
    }
    model, audit, outcome = run(scripts, tmp_path, interaction=True)
    refused = tool_outputs(model, "audit planner", 3)[-1]
    assert '"ok": false' in refused and "open the settings" in refused and "Call explore" in refused
    assert STATE in audit.state_paths and any(f.page_url == STATE and f.source == "agent" for f in audit.findings)
    assert '"ok": true' in tool_outputs(model, "audit planner", 6)[-1]
    assert not model.scripts["audit planner"] and not model.scripts["explorer"]


def test_each_goal_flow_needs_its_own_successful_exploration(tmp_path):
    report = {"states": [{"label": "settings-open", "how_reached": "clicked Open settings", "what_changed": "a form"}],
              "flows": [], "links_worth_auditing": [], "blocked": []}
    scripts = {
        "audit planner": [call("set_goal", {"pages": 1, "level": "AA", "flows": ["open the settings", "search"],
                                            "reason": "two flows"}),
                          call("audit_pages", {"page_keys": [PAGE], "focus": ""}),
                          call("explore", {"page_key": PAGE, "goal": "open the settings"}),    # fails: covers nothing
                          call("finish", {"reason": "done"}),
                          call("explore", {"page_key": PAGE, "goal": "Open the settings panel"}),  # covers one flow
                          call("audit_pages", {"page_keys": [STATE], "focus": ""}),
                          call("finish", {"reason": "done"}),
                          *[PLAN_DONE] * 3],   # it stops with "search" owed, and again on each of the two hand-backs
        "lead auditor": [FINAL, FINAL, judge("3.3.2"), FINAL, FINAL],
        "explorer": [{"states": "not a list"},                                  # an invalid report: the run fails
                     call("click_element", {"selector": "#open"}), call("label_page_state", {"label": "settings-open"}),
                     report],
        "reviewer": [confirm_all] * 2,
        "summary writer": [SUMMARY],
    }
    model, audit, outcome = run(scripts, tmp_path, interaction=True)
    assert '"error": "The explorer stopped early' in tool_outputs(model, "audit planner", 3)[-1]
    both = tool_outputs(model, "audit planner", 4)[-1]
    assert '"unexplored_flows": ["open the settings", "search"]' in both   # the failed exploration covered nothing
    one = tool_outputs(model, "audit planner", 7)[-1]
    assert '"unexplored_flows": ["search"]' in one and '"ok": false' in one
    assert [e["action"] for e in outcome.plan_log].count("planner_resumed") == 2
    assert not model.scripts["audit planner"] and not model.scripts["explorer"]


def test_the_fingerprint_tells_apart_states_that_differ_only_in_attributes_links_or_values():
    menu = """<!doctype html><html lang="en"><head><title>Shop</title></head><body><main><h1>Shop</h1>
<button id="menu" aria-expanded="false" aria-controls="items">Menu</button>
<ul id="items" hidden><li><a href="#a">Shoes</a></li><li><a href="#b">Bags</a></li></ul>
<label for="q">Search</label><input id="q"></main><script>
document.getElementById('menu').addEventListener('click', (ev) => {
  document.getElementById('items').hidden = false; ev.currentTarget.setAttribute('aria-expanded', 'true');
});
</script></body></html>"""

    async def scenario():
        from agent_access.agent_tools.browser_tools import fill, page_fingerprint
        async with AuditContext(screen_reader="emulated", tracing=False) as audit:
            await load_html_content(audit, menu, "file:shop.html")
            closed = await page_fingerprint(audit)
            await click(audit, "#menu")
            opened = await page_fingerprint(audit)
            await fill(audit, "#q", "shoes")
            filled = await page_fingerprint(audit)
            return closed, opened, filled

    closed, opened, filled = asyncio.run(scenario())
    assert closed["headings"] == opened["headings"] and closed["controls"] == opened["controls"]   # what it missed
    assert closed != opened and opened["links"] == closed["links"] + 2 and opened["states"] == ["BUTTON:Menu"]
    assert opened != filled and filled["values"] == ["shoes"]


def test_raising_the_level_reopens_pages_worked_to_a_stop(tmp_path):
    scripts = {
        "audit planner": [call("audit_pages", {"page_keys": [PAGE], "focus": ""}),            # worked to a stop at A
                          call("set_goal", {"pages": 1, "level": "AA", "flows": [], "reason": "a form needs AA"}),
                          call("finish", {"reason": "done"}),                                  # refused: AA is owed
                          call("audit_pages", {"page_keys": [PAGE], "focus": ""}),
                          call("finish", {"reason": "done"}), PLAN_DONE],
        "lead auditor": [FINAL] * 4,
        "reviewer": [confirm_all],
        "summary writer": [SUMMARY],
    }
    model, audit, outcome = run(scripts, tmp_path, level="A")
    refused = tool_outputs(model, "audit planner", 3)[-1]
    assert '"ok": false' in refused and PAGE in refused
    assert '"ok": true' in tool_outputs(model, "audit planner", 5)[-1] and not model.scripts["lead auditor"]


def test_the_floor_also_works_states_that_a_floor_run_added(tmp_path):
    scripts = {
        "audit planner": [PLAN_DONE] * 3,       # it stops at once, and again on each hand-back: the code floor runs
        "lead auditor": [call("click_element", {"selector": "#open"}), call("label_page_state", {"label": "settings-open"}),
                         FINAL, FINAL,          # the page: run 1 labels a state, run 2 is the continuation
                         FINAL, FINAL],         # the state it added, worked by the same floor
        "reviewer": [confirm_all] * 2,
        "summary writer": [SUMMARY],
    }
    model, audit, outcome = run(scripts, tmp_path, interaction=True)
    floored = [e["page"] for e in outcome.plan_log if e["action"] == "lead_floor"]
    assert floored == [PAGE, STATE] and not model.scripts["lead auditor"]


def test_label_state_hands_back_the_state_as_labelled_focus_included():
    """The sweep's shortcut check blurs the focused element; the next key press must still reach the field."""
    async def scenario():
        async with AuditContext(screen_reader="emulated", tracing=False) as audit:
            # Nothing for the sweep to toggle, so the page looks the same afterwards: only the focus has moved.
            await load_html_content(audit, '<!doctype html><html lang="en"><head><title>Search - Ferries</title></head>'
                                    '<body><main><h1>Search</h1><label for="q">Search</label><input id="q"></main>'
                                    '</body></html>', PAGE)
            await click(audit, "#q")
            await label_state(audit, "search-focused")
            return await (await audit.page()).evaluate("() => document.activeElement.id")

    assert asyncio.run(scenario()) == "q"


def test_a_state_is_rebuilt_only_when_its_steps_still_reach_it():
    async def scenario():
        async with AuditContext(screen_reader="emulated", tracing=False) as audit:
            await load_html_content(audit, HTML, PAGE)
            await click(audit, "#open")
            await label_state(audit, "settings-open")
            await audit.restore_page(PAGE)
            rebuilt = await audit.restore_state(STATE)
            panel = await (await audit.page()).locator("#mail").is_visible()
            origin_dirty = PAGE in audit.dirty                         # the browser shows the state, not the page
            audit.inline_html[PAGE] = HTML.replace("Settings panel", "Newsletter")   # the site changed meanwhile
            different = await audit.restore_state(STATE)               # the steps replay, but reach another page
            audit.inline_html[PAGE] = HTML.replace('id="open"', 'id="gone"')
            missing = await audit.restore_state(STATE)                 # a step's element no longer exists
            unknown = await audit.restore_state(PAGE + " [never labelled]")
            return rebuilt, panel, origin_dirty, different, missing, unknown, audit.current_url

    rebuilt, panel, origin_dirty, different, missing, unknown, cursor = asyncio.run(scenario())
    assert rebuilt and panel and origin_dirty
    assert not different and not missing and not unknown and cursor == PAGE   # never claims a state it didn't reach


def test_a_reused_label_for_a_different_state_keeps_both():
    async def scenario():
        async with AuditContext(screen_reader="emulated", tracing=False) as audit:
            await load_html_content(audit, HTML, PAGE)
            first = (await label_state(audit, "panel"))["page_url"]           # before the click
            await click(audit, "#open")
            second = (await label_state(audit, "panel"))["page_url"]          # same label, different state
            again = (await label_state(audit, "panel"))["page_url"]           # the same state again: same key
            return first, second, again, audit

    first, second, again, audit = asyncio.run(scenario())
    assert first == PAGE + " [panel]" and second == again == PAGE + " [panel #2]"
    assert audit.state_paths[first]["steps"] == [] and audit.state_paths[second]["steps"] == [{"do": "click", "selector": "#open"}]


def test_audit_pages_runs_every_page_and_state_given_in_one_call(tmp_path):
    """After an exploration the root's cursor is a state; each job must load its own page or rebuild its own state."""
    scripts = {
        "audit planner": [call("audit_pages", {"page_keys": [PAGE, STATE], "focus": ""}),
                          call("finish", {"reason": "done"}), PLAN_DONE],
        "lead auditor": [FINAL] * 4,      # two runs each; order-free, because the two run at the same time
        "reviewer": [confirm_all] * 2,
        "summary writer": [SUMMARY],
    }

    async def scenario():
        model = RouterModel(scripts)
        async with AuditContext(output_dir=tmp_path, screen_reader="emulated", tracing=False) as audit:
            await load_html_content(audit, HTML, PAGE)
            from agent_access.engine import run_sweep
            await run_sweep(audit)
            await click(audit, "#open")
            await label_state(audit, "settings-open")                 # the cursor is left on the state, as explore does
            outcome = await run_agentic_audit(audit, model=model, progress=lambda m: None)
            return model, outcome

    model, outcome = asyncio.run(scenario())
    audited = {e["page"]: e["lead_runs"] for e in outcome.plan_log if e["action"] == "audit_page"}
    assert audited == {STATE: 2, PAGE: 2} and outcome.errors == []
    assert '"ok": true' in tool_outputs(model, "audit planner", 2)[-1]


HTTP_PAGE = """<!doctype html><html lang="en"><head><title>Ferries</title></head><body>
<a href="#main">Skip to content</a><main id="main" tabindex="-1"><h1>Ferries</h1>
<button id="open">Open settings</button><button id="more" hidden>More options</button>
<section id="panel" hidden><h2>Settings panel</h2></section><section id="extra" hidden><h2>More options</h2></section>
</main><script>
document.getElementById('open').addEventListener('click', () => {
  document.getElementById('panel').hidden = false;
  document.getElementById('more').hidden = false;
  fetch('/api/opened', {method: 'POST', body: 'x'}).catch(() => {});
});
document.getElementById('more').addEventListener('click', () => { document.getElementById('extra').hidden = false; });
</script></body></html>"""


def test_chained_states_on_an_http_page_are_rebuilt_and_replays_count_no_new_safety_events(tmp_path):
    """Over HTTP, as CLAUDE.md asks for interaction changes. The page has a skip link, so the sweep on the first state
    moves the URL to #main; the second state must still be rebuildable. The click's POST is blocked once, as the
    agent's action; replaying it later is not another safety event."""
    import functools
    import http.server
    import threading

    from agent_access.agent_tools.browser_tools import open_url
    from agent_access.agent_tools.check_tools import run_sweep_remaining

    (tmp_path / "index.html").write_text(HTTP_PAGE, encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/index.html"

    async def scenario():
        async with AuditContext(screen_reader="emulated", tracing=False, measurement="lazy") as audit:
            await open_url(audit, url)
            await click(audit, "#open")
            first = (await label_state(audit, "settings"))["page_url"]
            await click(audit, "#more")
            second = (await label_state(audit, "more"))["page_url"]
            agent_blocks = sum(t.get("action") == "blocked" for t in audit.tripwires)
            await audit.restore_page(url)
            rebuilt = await audit.restore_state(second)
            page = await audit.page()
            extra = await page.locator("#extra").is_visible()
            after_blocks = sum(t.get("action") == "blocked" for t in audit.tripwires)
            await run_sweep_remaining(audit, url)                      # the origin is measured on the origin, not the state
            origin_shown = await page.locator("#panel").is_hidden()
            return audit, first, second, rebuilt, extra, agent_blocks, after_blocks, origin_shown

    try:
        audit, first, second, rebuilt, extra, agent_blocks, after_blocks, origin_shown = asyncio.run(scenario())
    finally:
        server.shutdown()
    assert audit.state_paths[second]["steps"] == [{"do": "click", "selector": "#open"}, {"do": "click", "selector": "#more"}]
    assert audit.state_paths[first]["fingerprint"]["url"] == url    # labelled before the sweep moved to #main
    assert rebuilt and extra
    assert agent_blocks == 1 and after_blocks == 1
    assert origin_shown


def test_a_browser_error_page_is_never_labelled_as_a_state():
    async def scenario():
        async with AuditContext(screen_reader="emulated", tracing=False) as audit:
            await load_html_content(audit, HTML, PAGE)
            await click(audit, "#broken")                            # nothing listens there: Chromium's error page
            result = await label_state(audit, "old-timetable")
            return audit, result, (await audit.page()).url

    audit, result, url = asyncio.run(scenario())
    assert "error" in result and "not a state of the site" in result["error"]
    assert audit.pages == [PAGE] and not audit.state_paths
    assert not url.startswith("chrome-error://") and audit.current_url == PAGE   # put back on the page it came from


def test_evals_allow_interaction_like_the_cli(monkeypatch):
    import agent_access.team
    from agent_access.team.orchestrator import AuditOutcome
    from evals.run_eval import run_case

    seen = []

    async def fake_run_audit(audit, **kwargs):
        seen.append(audit.allow_interaction)
        return AuditOutcome(report=None)

    monkeypatch.setattr(agent_access.team, "run_audit", fake_run_audit)
    case = {"name": "flow_explore", "file": "cases/flow_explore.html", "expected": {"3.3.2": "fail"}}
    for no_interaction in (False, True):
        args = Namespace(no_trace=True, measurement="eager", use_lessons=False, mode="code", model=None,
                         reviewer_model=None, skip_review=False, no_interaction=no_interaction)
        asyncio.run(run_case(case, args))
    assert seen == [True, False]
