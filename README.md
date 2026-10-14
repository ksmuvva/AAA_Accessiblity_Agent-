# Accessibility AI Agent

A multi-agent auditor that tests web pages against all **86 WCAG 2.2 success criteria** (Levels A, AA and AAA).
Automated checks measure the page in a real browser. AI agents then judge every criterion the automation can't
settle on its own, an independent reviewer re-checks the verdicts, and the result is an evidence-backed report.

Built on the [OpenAI Agents SDK](https://github.com/openai/openai-agents-python), Playwright (Chromium), axe-core
and, optionally, the NVDA screen reader.

> **This is an automated assessment, not a conformance certificate.** Criteria the agents can't decide stay
> `needs_review` rather than becoming a pass. Emulated screen-reader results don't replace testing with real
> assistive technology and people with disabilities.

## Contents

- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running an audit](#running-an-audit)
- [Reading the report](#reading-the-report)
- [Running audits through the harness](#running-audits-through-the-harness)
- [Safety and privacy](#safety-and-privacy)
- [Testing](#testing)
- [Evaluations](#evaluations)
- [Project structure](#project-structure)
- [Limitations](#limitations)

## How it works

Every audit has two layers: measurement in code, then judgement by agents.

**1. Measurement (deterministic).** For each page, *probes* collect raw data from the browser: the axe-core
results, the accessibility tree, colour contrast (the Colour Contrast Analyser method), keyboard focus order, forms,
hover and disclosure behaviour, media, and screen-reader output. Then **86 rules**, one per WCAG criterion, turn
that data into a result for each criterion: `fail`, `pass`, `needs_review` or `not_applicable`.

**2. Judgement (agents).** No criterion is closed by automation alone. A criterion stays open until it fails or an
agent has recorded a judgement for it.

```
Planner          decides which pages to audit, in what order, and when to finish (within the budget)
 └─ Lead auditor  works through every open criterion on a page; reads evidence, re-runs checks, judges
     ├─ Forms specialist        ┐
     ├─ Media specialist        │ take over the criteria in their domain via SDK handoffs,
     ├─ Navigation specialist   │ then hand back to the lead
     ├─ Explorer                │ clicks through the site to find states and flows to test
     └─ Verifier                ┘ re-measures a doubtful failure before it is recorded
Reviewer         re-checks every FAIL and every agent PASS; unconfirmed verdicts become needs_review
Summary writer   writes the overall assessment from the reviewed data only
```

When a criterion has no clear verdict, the auditor stays cautious. A failure the reviewer doubts becomes
`needs_review`, never a pass. A pass from automation alone also counts as `needs_review` until an agent confirms it.

## Requirements

- Python 3 (developed and tested on 3.14)
- An OpenAI API key. Audits make paid model calls.
- Chromium for Playwright (installed below)
- Optional, Windows only: Node.js 20+ and NVDA, for testing with a real screen reader

## Installation

```bash
git clone https://github.com/ksmuvva/Accessiblity_AI_Agent.git
cd Accessiblity_AI_Agent
python -m venv .venv
.venv/Scripts/activate            # Windows; on macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

Optional real-screen-reader support (NVDA, Windows):

```bash
cd nvda_bridge
npm install
node node_modules/@guidepup/setup/lib/index.js setup
node node_modules/@guidepup/setup/lib/index.js install nvda
```

## Configuration

Create a `.env` file in the project root (see `.env.example`):

```ini
OPENAI_API_KEY=sk-...
OPENAI_MODEL=            # optional: model for the auditing agents
OPENAI_REVIEWER_MODEL=   # optional: a different model for the reviewer, for a more independent check
```

Never commit `.env`.

## Running an audit

```bash
# One page, conformance target AA (the default)
python main.py https://example.com

# Up to 5 same-site pages, target AAA, headless and silent
python main.py https://example.com --pages 3 --max-pages 5 --level AAA --screen-reader emulated

# A local HTML file
python main.py --html-file path/to/page.html

# Walk through a multi-step process
python main.py https://shop.example.com --process "add an item to the basket and reach checkout"
```

| Option | What it does |
|---|---|
| `--level A\|AA\|AAA` | Conformance target (default `AA`). All 86 criteria are tested either way; those above the target are reported separately. |
| `--pages N` / `--max-pages N` | Minimum pages to audit (default 1) and the most the planner may open (default 10). |
| `--budget-calls N` | Model calls the whole run may spend (default 400; 60 are kept back for the reviewer and summary). |
| `--budget-minutes N` | Wall-clock minutes after which no new agent work starts (default 60). |
| `--screen-reader auto\|nvda\|emulated` | `auto` uses real NVDA when installed, which **opens a visible browser and speaks aloud**. `emulated` reads the accessibility tree and runs headless. |
| `--no-interaction` | Stop the agents from clicking, typing and following links. |
| `--allow-submit URL_PREFIX` | Let data-changing requests (POST etc.) through to URLs starting with this prefix. Repeatable. |
| `--mode agent\|code` | `agent` (default): the planner runs the audit. `code`: a fixed per-page flow. |
| `--model`, `--reviewer-model` | Override the models from `.env`. |
| `--memory PATH\|off` | SQLite memory of past runs on a site (default `reports/memory.sqlite`). |
| `--no-trace` | Don't send agent traces (page content and screenshots) to OpenAI. |
| `--skip-review` | Skip the reviewer. Cheaper, but less reliable. |

Run `python main.py --help` for every option.

### What a run costs

Cost depends on the page and the model. As a guide, a single test page with about 7 failures used 79 model calls
and took under 3 minutes. Multi-page audits scale roughly with the number of pages. Set `--budget-calls` to cap
spending. A budget that is too small ends the run early, and the criteria left unjudged are listed in the report.

## Reading the report

Each run writes to `reports/<host>-<timestamp>/`:

| File | Contents |
|---|---|
| `report.md` | Human-readable report: summary, top issues, and each criterion's status with evidence |
| `report.json` | The same data in machine-readable form, plus the review log, budget used, errors and warnings |
| `screenshots/` | Screenshots that support the findings |

Criterion statuses:

| Status | Meaning |
|---|---|
| `fail` | A defect observed directly on the page, and kept after review |
| `pass` | An agent judged the criterion met, and the reviewer confirmed it |
| `needs_review` | Not decided: automation passed but no agent confirmed it, the reviewer doubted a verdict, or the agents ran out of budget |
| `not_applicable` | The page has no content the criterion applies to (for example, no video) |
| `not_tested` | No check produced a result |

With several pages, each criterion shows its worst status across them. Read the **Warnings** section too: it lists
pages that weren't finished and criteria the agents left open.

## Running audits through the harness

The harness wraps the auditor for repeatable runs, batches and CI. Each job runs in its own process with a hard
timeout, and gets its own output folder containing a `manifest.json`.

```bash
python -m agent_access.harness_cli https://example.com --max-pages 1 --fail-threshold 0
python -m agent_access.harness_cli --jobs examples/harness_jobs.json
```

| Exit code | Meaning |
|---|---|
| `0` | Completed, within `--fail-threshold` |
| `1` | Completed, but more target-level criteria failed than `--fail-threshold` allows |
| `2` | Invalid input or configuration |
| `3` | The audit reported errors or timed out (this includes running out of budget) |
| `130` | Interrupted |

By default the harness runs headless with the emulated screen reader, memory off, tracing off and data-changing
requests blocked. See [HARNESS_README.md](HARNESS_README.md) for the full details.

## Safety and privacy

- **The agents interact with the site by default.** They click, type and follow same-site links. Requests that
  change data (POST, PUT, DELETE…) are blocked unless you allow a URL prefix with `--allow-submit`. Buttons that
  look destructive are skipped. Use `--no-interaction` to forbid interaction completely.
- **Only audit sites you own or have permission to test.**
- **Page content is treated as untrusted data.** The agents are told never to follow instructions found on a page,
  and a guardrail redacts common prompt-injection phrases from page content and records them in the report.
- **Traces go to OpenAI by default.** They include page content and screenshots; `--no-trace` turns this off.
- **Reports and logs contain content from the audited site.** Store them accordingly.

## Testing

The test suite needs no API key and makes no model calls. It uses local HTML fixtures, headless Chromium, the
emulated screen reader and a scripted fake model.

```bash
python -m pytest                                    # full suite, about 3 minutes (runs in parallel)
python -m pytest -n 0 tests/test_harness.py         # one file, without parallel workers
```

After changing a rule's name, technology, probes or judgement flag, regenerate the catalogue:

```bash
python -m agent_access.catalogue.sync
```

## Evaluations

The evals run the real agents against pages whose correct results are known, and report recall, precision, the
share of criteria decided, and false passes. **They use a real model and cost money.**

```bash
python -m evals.run_eval --model MODEL --fail-on-false-pass
```

## Project structure

```
main.py                 Entry point: python main.py URL
agent_access/
  cli.py                Command-line interface
  catalogue/            The 86 WCAG 2.2 criteria, with the manual checks for each
  probes/               Measure the page and return raw data, never verdicts
  rules/                One rule per criterion; the only place verdicts come from
  engine.py             Runs rules, sweeps pages, re-tests after interaction
  agent_tools/          Tools the agents call: browser, planning, reporting, checks
  team/                 Planner, lead, specialists, explorer, verifier, reviewer, summary writer, guardrails
  harness.py            Process-isolated runner for batches and CI
  memory.py             SQLite memory of sites and lessons
  js/, vendor/          Browser-side JavaScript and axe-core
nvda_bridge/            Node bridge to NVDA via Guidepup
evals/                  Evaluation runner and labelled test pages
tests/                  pytest suite and HTML fixtures
```

## Limitations

- Coverage depends on the model-call budget. With too few calls the run stops early and leaves criteria
  `needs_review`.
- Real NVDA testing works on Windows only. Startup is slow (40–150 s) and can be blocked by Windows focus
  protection; the auditor then falls back to emulation and records why.
- Live-region announcements can't be captured from NVDA, so Status Messages (4.1.3) is judged from DOM changes
  instead.
- Pages behind a login, CAPTCHA or heavy bot protection may not load.
- Criterion 4.1.1 Parsing is obsolete in WCAG 2.2 and is not tested.
