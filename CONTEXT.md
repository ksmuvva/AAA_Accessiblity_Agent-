# Context

What a new engineer or Claude session needs before changing Agent_Access, plus the full **code review of 2026-10-21**.
How to install, run and test: [README.md](README.md). Contracts and gotchas: [CLAUDE.md](CLAUDE.md). What the system
must do and how far the code meets it: [REQUIREMENTS.md](REQUIREMENTS.md).

## What this is

A CLI that audits websites against all 86 WCAG 2.2 success criteria. Code measures each page in headless Chromium
(axe-core, Siteimprove Alfa, its own probes, an emulated or real NVDA screen reader). AI agents on the OpenAI Agents SDK
judge what code can't decide. Output: `report.md`, `report.json`, a draft VPAT-style ACR (`acr.html`) and screenshots.
One person runs it on their own machine, or CI runs it through the harness; a run covers 1 to about 100 pages.

## State on 2026-10-21

- `main` is at `ed6a9d2` (PR #45). PR #46 (`docs/claude-md-default-mode`) adds CLAUDE.md tooling notes and these files.
- About 19,400 lines of Python, 39 test files, 326 test functions. CI: `.github/workflows/tests.yml`, Ubuntu and
  Windows, Python 3.13, installs with `-c constraints.txt`.
- Pinned in `constraints.txt`: `openai-agents==0.24.0`, `openai==3.33.0`, `playwright==1.64.0`, `pydantic==2.14.1`.
  The code relies on SDK internals (run hooks, tool guardrails) and Playwright 1.64 routing, so upgrade through CI.
- Default CLI mode is `single`: one auditor agent, **no reviewer**. `agent` and `code` modes run the full team.
- `JOURNEYS_DESIGN.md` is a proposal; nothing in it is built.

## Words used in this code

| Term | Meaning |
|---|---|
| Criterion | One WCAG 2.2 success criterion, e.g. 1.4.3. 86 are active; 4.1.1 is removed. |
| Probe | Measures the page and returns raw data, never a verdict (`agent_access/probes/`). Cached once per page key. |
| Rule | One synchronous function per criterion that turns probe data into findings (`agent_access/rules/`). |
| Measured / judgement rule | `judgement=False`: code can decide. `judgement=True` (60 rules): an agent must judge. |
| Finding | One result for a criterion on a page: status, source (`axe`, `custom`, `alfa`, `agent`), evidence, selectors. |
| Status | `fail`, `pass`, `needs_review`, `not_applicable`, `not_tested`. Precedence is in CLAUDE.md. |
| Page key / state key | A page's URL (or inline label). A state key is `page [label]`, a labelled state of that page. |
| Labelled state | A page after interaction (menu open, step 2), saved with the steps that reach it so it can be rebuilt. |
| Dirty | The page was changed by interaction since it loaded; cached results describe the page as loaded. |
| Fork | A child `AuditContext` with its own browser page that shares all results with the root. |
| Floor | Code that runs whatever the agents skipped (every target-level check before a page is done). |
| Eager / lazy | Eager: all probes and rules run on load. Lazy (CLI default): agents choose; the floor runs the rest. |
| Coverage contract | A criterion stays open until it fails or an agent records a judgement. |
| Blind review | The reviewer gets the question and evidence, not the auditor's verdict, so it judges instead of agreeing. |
| Network guard | The browser-context route that blocks writes, off-site and destructive navigation during interaction. |
| Tripwire | A logged safety event (blocked request, prompt injection, destructive click refused). |
| Budget | Model-call, minute and page limits; `reserve_calls` are kept for the planner, reviewer and summary. |
| ACR | Accessibility Conformance Report, the VPAT 2.5Rev WCAG table generated as `acr.html`. |

## How a run works

1. `main.py` → `cli.main`: parse and check flags, open `AuditContext` (browser, guard, memory), then `run_audit`.
2. **single** (`auditor.py`): per page the agent calls one check tool per target criterion and records results; the
   floor runs the rest; same-site links in order. **code** (`team/orchestrator.py`): measure, then the lead and
   specialists judge each page in turn. **agent** (`team/planner.py`): a planner agent picks pages, flows and when to
   finish, within the budget.
3. Team modes only: the reviewer re-checks verdicts; the summary writer writes the overview.
4. Every mode: site tests (3.2.3/3.2.4/3.2.6), linked PDFs (veraPDF), UK statement, then `report_writer` and `acr`.

## Where to look

| Concern | Files |
|---|---|
| Browser, forks, guard, findings store | `agent_access/context.py` |
| Opening pages, clicks, address checks | `agent_access/agent_tools/browser_tools.py` (`refused`, `open_url`, `_guarded`) |
| Running rules, caching, sweeps, floors | `agent_access/engine.py`, `agent_access/probes/__init__.py`, `agent_access/agent_tools/check_tools.py` |
| Status aggregation, agent judgements | `agent_access/agent_tools/report_tools.py`, `agent_access/agent_tools/planning.py` |
| Agents | `agent_access/team/` (planner, orchestrator, audit_agent, specialists, explorer, verifier, reviewer, summarizer, sr_user, guardrails, runtime, handoffs) |
| Reports | `agent_access/report_writer.py`, `agent_access/acr.py`, `agent_access/models.py` |
| Memory, budget, harness | `agent_access/memory.py`, `agent_access/budget.py`, `agent_access/harness.py`, `agent_access/harness_cli.py` |
| Evals | `evals/run_eval.py`, `evals/compare.py`, `evals/act_eval.py`, `evals/cases.json` |

## Trust boundaries

- **Untrusted:** everything from an audited site (DOM text, attributes, redirects, scripts' behaviour) and every model
  output. Both flow into prompts, memory, `report.md`, `report.json` and `acr.html`.
- **Checked at:** `refused()` before opening a URL; `network_guard`/`_judge` per request during interaction;
  `team/guardrails.py` on tool input and output; `memory._scrub` before storing; `html.escape` in `acr.py`.
- **Secrets in play:** `OPENAI_API_KEY` (`.env`), the `--storage-state` file (session cookies), and anything an agent's
  `evaluate_js` reads from the page.

## Code review 2026-10-21

**Method.** Read-only review of commit `ed6a9d2`, by five parallel reviewers (safety; result correctness; concurrency
and budget; WCAG rules and probes; memory, harness, evals and rendering), with every top claim re-checked against the
code. Graphify (`ksmuvva/AAA_Accessiblity_Agent-`) was used to rank hotspots; claude-mem had no history yet.
63 core tests (`test_honest_results`, `test_planning`, `test_wcag_tools`, `test_safeguards`, `test_auditor`) passed in
2 min 46 s **with these bugs present**; the full suite did not finish within 25 minutes on the review machine.

**Verdict.** Careful design, but not yet safe to point at real sites, and some reported passes are wrong. Fix F1–F5
(safety) and F39, then F6–F14 (wrong results), each with a test that fails without the fix.

### Safety and privacy

| # | Finding | Where | Smallest fix |
|---|---|---|---|
| F1 | Redirects skip every check: `open_url` and clicks follow a 302 to another host or to `169.254.169.254` (cloud keys); that page is audited and reported. | `agent_tools/browser_tools.py:63-95`, `context.py:442-457` | Re-run `refused()` on the final `page.url`; on refusal go to `about:blank`. |
| F2 | Address check holes: `127.1`, `0x7f.0.0.1`, `0177.0.0.1` count as public (verified); an `--html-file` start allows every URL; `--allow-offsite` drops the private check. Also hits PDF and statement fetches. | `browser_tools.py:39-55`, `context.py:451` | `socket.inet_aton` fallback; always refuse private hosts; refuse all http(s) when there is no start site. |
| F3 | The sliders probe presses keys with no network guard, so a save-on-change slider writes to the live site. | `probes/interaction.py:298-315` | Wrap in `network_guard()`, then `_reset()`. |
| F4 | Destructive check reads only the path: `wp-login.php?action=logout` passes and ends the signed-in session. | `context.py:448`, `probes/interaction.py:355` | Check `f"{path} {query}"`, as `refused()` does. |
| F5 | Script results and blocked-request URLs keep cookies and tokens, written into `report.md`/`report.json`. | `agent_tools/verify_tools.py:239-242`, `report_writer.py:83-93`, `context.py:398,450` | Redact cookie/storage values and token-like strings before storing. |
| F18 | The injection scrub runs on JSON-escaped text, so `Ignore all<br>previous instructions` (`\n` in JSON) is not redacted. | `team/guardrails.py:76-98`, `context.py:67` | Scrub string values before `json.dumps`. |
| F26 | The destructive-click guardrail can't see `<img alt>`, `aria-labelledby` or SVG titles, describes the iframe not the focused button, allows `Shift+Enter`, and allows a target it couldn't describe in 2 s. | `intent.py` (DESCRIBE_JS), `team/guardrails.py:104-153` | Describe by accessible name; match the key's last part; refuse undescribed targets. |
| F27 | Writes slip through: `fetch(new Request(url, {keepalive:true}))` skips the keepalive block; `fresh` turns on before navigation, so the old page can write while unloading. | `js/page/safety.js`, `browser_tools.py:70`, `context.py:360,381` | Check `Request.keepalive`; set `fresh` on main-frame commit. |
| F39 | Page and model text goes into `report.md` unescaped: a description with `\n## Passed` forges a heading; `<details>` hides the rest on GitHub. (`acr.html` is escaped correctly.) | `report_writer.py:43-49,104,153,215-254` | Pass every page/model field through `_md_escape`, and escape `<`. |
| F41 | The `remember` tool logs a prompt injection as "rejected" but stores the note anyway. | `memory.py:450-456` | Return an error after the tripwire. |

### Wrong results

| # | Finding | Where | Smallest fix |
|---|---|---|---|
| F6 | Single mode has no reviewer, but the ACR says every verdict was "re-checked by a reviewer agent" and the report says "Passed (reviewer confirmed)". | `acr.py:93-96`, `report_writer.py:228` | Choose the text by `audit.verdicts_reviewed`; copy `report.notes` into the ACR. |
| F7 | An agent can pass any of the 60 judgement criteria before its check ran; that pass then outranks the check's later needs_review findings. | `agent_tools/report_tools.py:46,122` | Drop `not tool.judgement and` at line 46. |
| F8 | Contrast is rounded before comparing: `#0078d7` on white (4.4989:1) passes 4.5:1, `#959595` (2.9953:1) passes 3:1 (verified). | `probes/contrast.py:52` | Compare unrounded; round only for display. |
| F9 | In lazy mode, checks run after a click measure the changed page and are cached as the page's own, shared with every fork. | `probes/__init__.py:49-61`, `check_tools.py:112-144`, `engine.py:215-230` | Refuse `run_checks`/`retest` while the page is dirty. |
| F10 | Cross-page rules count a page's labelled states as pages, so a one-page audit passes 3.2.3/3.2.4/3.2.6. | `rules/understandable.py:265` | Add `and not is_state_key(p)`. |
| F11 | Flashes are averaged over 2 s (6 in one second → 3.0 → no fail); flash area is summed across the whole screen. | `probes/layout.py:131`, `rules/operable.py:252-302` | Worst single second from the collected times; area within one 341×256 region. |
| F12 | 4.1.2 fails valid roles (tabpanel, region, treeitem, toolbar…) as "no control role". | `rules/robust.py:28-37`, `probes/screen_reader.py:267` | Add them to `WIDGET_ROLE_WORDS`. |
| F13 | Unknown `lang` codes are assumed Latin (Macedonian, Hebrew `iw`, Pashto fail); `sr-Latn` fails because script subtags are ignored. | `rules/understandable.py:23-25,73-76` | Honour `-Latn/-Cyrl/-Arab/...`; add the codes. |
| F14 | "Nothing tested" records pass: 1.4.13 with 0 triggers or a timed-out hover; 1.3.5 with only a search box; 3.3.7 from one page. | `rules/perceivable.py:345,602-605`, `rules/understandable.py:513` | Not applicable when 0 tested; needs_review on probe failures. |
| F15 | Targets inside any `tabindex`/`onclick` container are dropped, so tiny icon buttons in a focusable panel pass 2.5.8. | `js/.../target_size.js:2-8` | Only links and buttons swallow their descendants. |
| F21 | 3.2.6 compares help links only with each other, not their position, so header-then-footer "Contact us" passes. | `rules/understandable.py:287-299,361-367`, `nav.js:33` | Tag each help item with its region. |
| F22 | Any CAPTCHA on the page fails 3.3.9, even without a sign-in, and invisible reCAPTCHA v3 counts. | `inventory.js:150`, `rules/understandable.py:520-532` | Only a visible CAPTCHA with a password field; 3.3.9 needs_review. |
| F23 | 4.1.3 treats `aria-describedby` text as announced; it is only read when the field gets focus. | `rules/robust.py:58-63` | Drop `and not fx["described"]`. |
| F25 | 1.4.12 only looks at `overflow: hidden`, so a fixed-height box that overflows and overlaps passes. | `rules/perceivable.py:575-579`, `probes/layout.py:43-55` | No-clip result is needs_review, or detect `scrollHeight > clientHeight`. |
| F29 | When a rule raises, the axe/Alfa merge and the pass→needs_review downgrade are skipped. | `engine.py:77-109` | Apply both in the `except` path. |
| F30 | Single mode records a not-applicable even when a disclosure revealed content nobody judged. | `agent_tools/report_tools.py:52-56` | Record needs_review instead. |
| F31 | The reviewer's contradiction check covers a confirmed PASS with a defect seen, not a confirmed NOT_APPLICABLE. | `team/reviewer.py:215-226` | Include `Status.NOT_APPLICABLE`. |
| F37 | "not fully compliant" in a UK statement is read as "fully compliant" (verified). | `statement.py:87` | Match `not fully` first; map it to partially. |
| F42 | Statement review date: a future date passes, 13 months passes, redirects stop at 4 (PDFs allow 5). | `statement.py:106-107,142` | `0 <= days <= 366`; `range(MAX_REDIRECTS + 1)`. |
| F46 | 1.4.4 accepts a 0.6 scale ratio (about 124% growth at 200% zoom); target size allows 0.5 px under 24. | `rules/perceivable.py:29,451`, `target_size.js:10` | Tighten after checking the ACT examples. |

### Reliability and lost work

| # | Finding | Where | Smallest fix |
|---|---|---|---|
| F16 | The planner's final floors aren't guarded; one "Execution context was destroyed" ends the run with no report. | `team/planner.py:731-741`, `team/orchestrator.py:132,138` | Move lines 132/138 inside the `try`; wrap `run_lead_on` at 735. |
| F24 | The sequential fallback can't rebuild a labelled state, so the second specialist on a state never runs. | `context.py:560-567` | Use `audit.return_to(key)`. |
| F28 | `finish` and the floors retry failing reloads on every call and add the same warning each time. | `check_tools.py:83-93`, `planner.py:286,589,737` | Skip a warning already present; de-duplicate `coverage_warnings`. |
| F35 | On Windows a harness timeout or cancel runs `taskkill /F` at once (no SIGTERM handler there), so the partial report is lost; the test is skipped on win32. | `harness.py:118-123`, `cli.py:186-187`, `tests/test_harness.py:149` | `CREATE_NEW_PROCESS_GROUP` + `CTRL_BREAK_EVENT`, grace, then kill; handle `SIGBREAK`. |
| F36 | Mapping veraPDF results runs outside the `try`; a KeyError drops the summary, review log and statement. | `documents.py:130-133` | Map inside the `try` with `files.get`. |
| F38 | Single mode silently ignores `--reviewer-model`, `--skip-review`, `--delegation`, and never writes memory; docstring examples are broken. | `cli.py:7-8,131-132`, `team/orchestrator.py:287-292,356-360` | `parser.error` for those flags in single mode; write memory in `audit_site`. |
| F43 | Report folders have 1-second names, so two runs of a host in the same second overwrite each other; a missing `--html-file` crashes after Chromium starts. | `cli.py:150-153,177` | Add a short uuid; `parser.error` if the file is missing. |
| F44 | Harness status `incomplete` isn't documented; an `OSError` creating a job folder stops the whole batch with exit 2. | `harness.py:159,174-175`, `harness_cli.py:286` | Document it; create the folder inside the per-job `try`. |
| F45 | If NVDA's stop fails, the bridge exits without trying again, so NVDA keeps running. | `nvda_bridge/bridge.mjs:253-256` | Set `stopped` only after a successful stop. |

### Budget and scale

| # | Finding | Where | Smallest fix |
|---|---|---|---|
| F17 | Single mode holds back 60 calls for a reviewer it never runs (`--budget-calls` must even exceed 60); parallel pages skip `admit_page`; after `BudgetExhausted` the reviewer still reloads every page. | `cli.py:115,241`, `budget.py:27`, `planner.py:216,277`, `reviewer.py:258-287` | `reserve_calls=0` in single mode; an `admitted` set; stop the review loop on `BudgetExhausted`. |
| F33 | Closed forks stay in `_by_page`, keeping their pages and data alive for the whole run (about 1,500 on 100 pages). | `context.py:212,477-488` | Pop the fork's page in `close()`. |
| F34 | Every rule run and open-items call scans all findings of the run; quadratic over 100+ pages. | `context.py:297-300`, `report_tools.py:57,67,156-159` | Index findings by page. |
| F32 | Lessons with `run:` hosts are never pruned or recalled; lessons per host are unbounded. | `memory.py:116,168-171,291` | Delete old `run:%` lessons on open; cap per host. |

### Evals, setup and lean

| # | Finding | Where | Smallest fix |
|---|---|---|---|
| F40 | `evals.compare` keeps only the last repeat of each case, so `--repeats 5` diffs one run. | `evals/compare.py:47-48` | Group by case name; compare counts. |
| F19 | README and CLAUDE.md install without `-c constraints.txt` (only CI pins); `session.json` from `--save-login` isn't git-ignored. | `README.md:74`, `CLAUDE.md` Commands, `.gitignore` | Add `-c constraints.txt`; ignore `session*.json`. |
| F20 | Copies to merge or delete (about −110 lines): closing tail ×3 (`orchestrator.py:362`, `planner.py:762`, `auditor.py:123`); lead-run loop ×2; `AGENT_ERRORS`/`_err` ×2; redirect loop ×2 (`documents.py`, `statement.py`); sRGB formula ×2; `NAVIGATION_TIMEOUT_MS` ×2; two fingerprint scripts; `TABLE_ORDER` = `SEVERITY`; stale `measurement_mode` `getattr` shims; `except ImportError` that can never fire; `harness.py:83` no-op `Path(...)`; pass-throughs `handoff_log`, `_owner`, `orchestrator.measure`. | as listed | Keep one of each. |

**Checked and sound:** luminance formula, large-text and text-spacing values, reflow viewport, target spacing
geometry, status precedence and worst-across-pages, inheritance counted once, reviewer batching, 4.1.1 removal, ACR
escaping and "Not Evaluated" only at AAA, memory SQL parameterised and host-scoped, harness subprocesses without a
shell, eval metrics (recall, precision, Wilson interval), no lock held by delegate tools.

**Not checked:** the full test suite (stopped after 25 min); runs against live sites; NVDA, EA IRIS and veraPDF paths;
most browser JavaScript under `agent_access/js/`; `evals/site_check.py`. Findings come from reading the code; only the
contrast numbers, the address parsing and the statement regex were reproduced.

## Open decisions

- **Journeys** (`JOURNEYS_DESIGN.md`): which persona first, opt-in or default, who names journeys, whether a walker may
  ever record a pass (proposed: no).
- **Single mode without a reviewer:** keep it as the default, or add the reviewer once F6/F7 are fixed.
- **Windows harness stop** (F35): graceful stop, or document that a timeout loses the report.

## Working on this repo

- Work on a branch and open a PR; the owner merges (CLAUDE.md).
- Every fix comes with a test that fails without it: the suite was green with F7–F15 present.
- Tools for Claude sessions (Graphify, Ponytail, skills by task, Context7): CLAUDE.md, "Tools for Claude sessions".
