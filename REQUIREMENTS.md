# Requirements

What Agent_Access must do, collected from the README, CLAUDE.md, HARNESS_SPEC.md, the CLI (`agent_access/cli.py`) and
the code's own docstrings. Each requirement has an ID and a **status from the code review of 2026-10-21** (commit
`ed6a9d2`). Finding numbers (F1, F2, ...) point to [CONTEXT.md](CONTEXT.md#code-review-2026-10-21).

Status: **Met** (read in the review; the code does it, no defect found) · **Partly** (it does it, with known gaps) ·
**Not met** (the code contradicts it) · **Not checkable** (a prompt rule; only evals measure it) · **Not run** (read,
but needs software the review couldn't run) · **Proposed** (designed, not built).

## 1. Purpose and scope

Agent_Access audits web pages against all 86 WCAG 2.2 success criteria (Level A, AA and AAA). Code measures each page in
a real browser; AI agents judge what measurement can't decide; the result is an evidence-backed report and a draft
Accessibility Conformance Report (ACR).

- It is an **automated assessment, not a conformance certificate**. Criteria it can't decide stay `needs_review`.
- **Users:** an accessibility tester or developer auditing a site they own or may test, from a CLI on their machine,
  or in CI through the harness.
- **Expected load:** one run at a time on one machine; a run may cover 1 to about 100 pages.
- **Out of scope:** a web UI, testing sites without permission, replacing tests with disabled people and real assistive
  technology, 4.1.1 Parsing (removed from WCAG 2.2).

## 2. Inputs and configuration

| ID | Requirement | Status |
|---|---|---|
| IN-1 | Accept one http(s) URL or a local HTML file (`--html-file`); reject anything else before a browser starts. | Met |
| IN-2 | `--level A\|AA\|AAA` (default AA) sets the target; criteria above it are reported as `beyond_target`. | Met |
| IN-3 | Single and code modes: `--pages N` (default 1) is the most same-site pages audited. Agent mode: `--pages` is the minimum and `--max-pages` (default 10) the ceiling. | Met (`--max-pages` does nothing outside agent mode) |
| IN-4 | `--sample wcag-em [--seed N]` picks one page per URL template plus a seeded random 10%, recorded in the report. | Met |
| IN-5 | `--process "..."` walks a multi-step process from the restored first page. | Met |
| IN-6 | `--save-login FILE` saves a signed-in session; `--storage-state FILE` audits behind the sign-in; a redirect to a sign-in form is a warning. | Partly (F19: the file isn't git-ignored) |
| IN-7 | Read `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_REVIEWER_MODEL` from `.env`; nothing else. `--model` / `--reviewer-model` override them. | Met |
| IN-8 | `--mode single` (default) \| `agent` \| `code`; `--measurement lazy` (default) \| `eager`; `--delegation handoffs\|tools`. | Met |
| IN-9 | Budgets: `--budget-calls` (default 400), `--budget-minutes` (default 60), `--max-pages`; no new agent work after the limit. | Partly (F17) |
| IN-10 | Every accepted flag has an effect in the chosen mode, or is rejected with a usage error (exit 2). | Not met (F38, F43) |

## 3. Measurement

| ID | Requirement | Status |
|---|---|---|
| ME-1 | Exactly one rule per active WCAG 2.2 criterion: 86 rules with the owner-specified names (`tests/test_wcag_tools.py::EXPECTED_NAMES`). | Met |
| ME-2 | Probes return raw data, never verdicts; rules are synchronous and read only the probes in their `uses`. | Met |
| ME-3 | Each probe runs once per page and is cached; forks share the cache and measure once. | Met |
| ME-4 | Rules only ever see **default-state** (as-loaded) page data; results from an interacted page are never cached as the page's. | Not met (F9) |
| ME-5 | Lazy measurement: before a page is done, code runs every target-level check no agent ran. Above-target checks run only when asked and are otherwise reported as not measured. | Met |
| ME-6 | Thresholds follow the WCAG 2.2 normative text (contrast 4.5:1 / 3:1 / 7:1 compared unrounded; flashes per any one second; target 24×24 CSS px; reflow at 320 CSS px; text-spacing values). | Partly (F8, F11, F15, F25, F46) |
| ME-7 | A check that measured nothing records not applicable or needs review, never pass. | Not met (F14) |
| ME-8 | A failed or crashed probe or rule never yields a clean pass; its error is recorded. | Partly (F29) |
| ME-9 | axe-core results apply to `AXE_CRITERIA`; Siteimprove Alfa only adds needs_review, never fails; Alfa skips pages over 3,000 elements. | Met |
| ME-10 | Real NVDA via Guidepup when available; any NVDA error falls back to the emulated reader, recorded in `engines`. Empty NVDA output is never read as "no name/role". NVDA is stopped when the run ends. | Not run (F45) |
| ME-11 | 4.1.1 Parsing is not tested: axe `wcag411` results are dropped and recording it is refused. | Met |
| ME-12 | Rule verdicts are correct for valid markup: valid ARIA roles, all `lang` values and script subtags. | Not met (F12, F13, F21, F22, F23) |

## 4. Agent judgement and review

| ID | Requirement | Status |
|---|---|---|
| AG-1 | **Coverage contract:** a criterion stays open on a page until it fails or an agent records a judgement for it. | Met |
| AG-2 | An agent records `fail` only for defects observed directly, and `needs_review` only when the deciding evidence is out of the tools' reach. | Not checkable |
| AG-3 | An agent may record a pass only after the criterion's check has run on that page state. | Not met for the 60 judgement criteria (F7) |
| AG-4 | An agent `not_applicable` is refused until the disclosures probe ran in full and revealed content was judged. | Partly (F30: single mode records it instead) |
| AG-5 | Agent fails are never overwritten by agents; only the reviewer can overturn them, and then only to `needs_review`. | Met |
| AG-6 | Team modes: the reviewer re-checks every FAIL and every agent PASS / NOT_APPLICABLE in batches of 25; blind review for judgement passes and all not-applicables; an unconfirmed verdict becomes `needs_review`, never `fail`. | Partly (F31) |
| AG-7 | Specialists, explorer, verifier and screen-reader user run within the budget and hand back to the lead; what they leave stays open. | Partly (F24) |
| AG-8 | Delegate tools never hold `audit.lock` (no deadlock). | Met |
| AG-9 | Every stage tolerates failure: errors go to `outcome.errors`, and a report is still written. | Not met (F16, F36) |
| AG-10 | The planner, reviewer and summary writer keep `reserve_calls`; working agents stop at the soft limit. | Partly (F17) |

## 5. Results and reports

| ID | Requirement | Status |
|---|---|---|
| RE-1 | Write `reports/<host>-<timestamp>/` with `report.md`, `report.json`, `acr.html` and `screenshots/`; one run never overwrites another. | Partly (F43) |
| RE-2 | Status precedence: fail > reviewer-demoted fail > agent pass > needs_review > not_applicable > automated pass only (needs_review) > not_tested. Across pages the worst status wins. | Met |
| RE-3 | **Doubt about a failure never becomes a pass**; no pass is reported without evidence an agent saw. | Partly (F7, F10, F14) |
| RE-4 | Labelled states inherit unchanged results from their page and are counted once site-wide. | Met |
| RE-5 | Cross-page criteria (3.2.3, 3.2.4, 3.2.6) record needs_review on a single page; states are not pages. | Not met (F10) |
| RE-6 | The report and ACR state truthfully how results were verified (no reviewer claim when none ran). | Not met (F6) |
| RE-7 | The ACR follows the VPAT 2.5Rev WCAG layout; undecided criteria are "Not determined", "Not Evaluated" only at AAA; page text is HTML-escaped. | Met |
| RE-8 | Coverage warnings list unfinished pages, criteria left open and checks never measured. | Met (F28: duplicates) |
| RE-9 | Linked same-site PDFs are checked against PDF/UA-1 with veraPDF, or listed as a coverage warning. | Partly (F2, F36; veraPDF not run) |
| RE-10 | UK accessibility statement checked on UK public-sector hosts or with `--uk-statement`. | Partly (F2, F37, F42) |
| RE-11 | Every agent script (`evaluate_js`) is logged with an id, cited as evidence and listed in the report. | Met (but see SA-7) |
| RE-12 | Page or model text can't change the structure of `report.md` (no forged headings, no raw HTML). | Not met (F39) |

## 6. Safety and privacy

| ID | Requirement | Status |
|---|---|---|
| SA-1 | No data-changing request (POST, PUT, DELETE, form submit, beacon, WebSocket send) reaches a site unless its URL starts with an `--allow-submit` prefix. | Partly (F3, F27) |
| SA-2 | Navigation stays on the audited site unless `--allow-offsite`, including after redirects. | Not met (F1) |
| SA-3 | Never open private, loopback, link-local or local addresses (SSRF), in any mode. | Not met (F1, F2) |
| SA-4 | Destructive-looking actions (delete, logout, unsubscribe, buy) are refused, whether the intent is in a label, path or query string. | Partly (F4, F26) |
| SA-5 | Page content is untrusted data: instruction-like text is redacted before it reaches a model or memory, and recorded truthfully. | Partly (F18, F41) |
| SA-6 | Traces (page content, screenshots) go to OpenAI only with `--trace`. | Met |
| SA-7 | Reports, manifests, memory and logs never contain API keys, cookies or session tokens. | Not met (F5) |
| SA-8 | `--no-interaction` forbids all clicking, typing and link following. | Met |
| SA-9 | Memory recalls page-derived text only for its own host; only `source="eval"` lessons are global; all stored text is scrubbed; memory stays bounded. | Partly (F32, F41) |
| SA-10 | `--memory PATH` stores what the run learned. | Not met in single mode (F38) |

## 7. Harness (HARNESS_SPEC.md)

| ID | Requirement | Status |
|---|---|---|
| HA-1 | Validate level, page and call limits, timeout and source before starting a browser or a model call. | Met |
| HA-2 | Default to headless, emulated screen reader, same-site, writes blocked, tracing off, memory off. | Met |
| HA-3 | A hard wall-clock timeout outside the soft budget; browser resources closed on completion, cancellation or failure; a timed-out job keeps its partial report. | Partly (F35: on Windows the worker is killed at once) |
| HA-4 | A unique output folder per job with `manifest.json` (config, UTC times, duration, artifacts, outcome), never API keys. | Met |
| HA-5 | Separate completed, timed-out and failed runs, each documented; no automatic retry of model calls. | Partly (F44: `incomplete` undocumented) |
| HA-6 | Batch jobs run in order, continue after a failure, write a batch summary; malformed batch config is rejected first. | Partly (F44: a folder error stops the batch) |
| HA-7 | Exit codes: 0 within `--fail-threshold`, 1 over it, 2 invalid input, 3 execution failure or timeout, 130 interrupted. | Met |

## 8. Evaluations

| ID | Requirement | Status |
|---|---|---|
| EV-1 | `evals.run_eval` scores recall, precision, share decided and false passes against `evals/cases.json`, recording commit, settings and model calls; `evals.compare` diffs two runs. | Partly (F40: compare uses only the last repeat) |
| EV-2 | `--max-calls` (default 1000) caps a run (exit 5); out of credits stops the run (exit 6). | Met |
| EV-3 | A no-model baseline (`--mode baseline`) runs in CI and must stay scorable (exit 3 if not). | Met |
| EV-4 | `evals.act_eval` scores the rules on the W3C ACT examples without a model. | Met (read, not run) |

## 9. Non-functional

| ID | Requirement | Status |
|---|---|---|
| NF-1 | Runs on Windows and Linux; Python 3.13 (CI) and 3.14 (development); Node 20 for the NVDA bridge. | Met |
| NF-2 | Install the versions in `constraints.txt` (the code relies on SDK internals and Playwright 1.64 routing). | Partly (F19: README and CLAUDE.md omit `-c constraints.txt`) |
| NF-3 | The test suite needs no API key, makes no model calls and doesn't start NVDA; about 5 minutes with 6 workers. | Partly (the full suite exceeded 25 min on the review machine; 63 core tests passed in 2 min 46 s) |
| NF-4 | Every rule has a fixture page it fails on; every bug fix adds a test that fails without it. | Partly (the suite passes with F7–F15 present) |
| NF-5 | Long runs (100+ pages) keep memory and per-call work bounded. | Partly (F33, F34) |
| NF-6 | Code stays minimal: no duplicated flows, dead fallbacks or pass-through wrappers (CLAUDE.md, "Simplicity First"). | Partly (F20) |

## 10. Proposed, not built

| ID | Requirement | Source |
|---|---|---|
| PR-1 | Journeys: personas (keyboard only, screen reader, 400% zoom) walk a task; barriers are recorded as fail or needs_review, never pass, citing the journey. Opt-in `--journeys`. | JOURNEYS_DESIGN.md (decisions 1–4 open) |
