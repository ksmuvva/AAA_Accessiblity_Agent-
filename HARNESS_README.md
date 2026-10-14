# Accessibility agent harness

The harness runs the existing website auditor in an isolated worker process. It adds validated job configuration, a wall-clock deadline, sequential batches, logs and machine-readable run manifests. It uses the existing planner, specialists, reviewer and WCAG reports.

## Setup

Start with [the main README's first agent test](README.md#start-your-first-agent-test) for installation, API key setup, a local test audit and troubleshooting.

Run these commands in PowerShell from the project root. Skip environment creation if `.venv` already exists:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
if (!(Test-Path -LiteralPath .env)) { Copy-Item -LiteralPath .env.example -Destination .env }
notepad .env
```

Fill in `OPENAI_API_KEY` and save `.env`. Live audits make paid model calls. The harness tests do not. Activation is unnecessary when using the explicit interpreter path below.

## Run

```powershell
.\.venv\Scripts\python.exe -m agent_access.harness_cli https://example.com --level AA --max-pages 1
.\.venv\Scripts\python.exe -m agent_access.harness_cli --html-file tests/fixtures/bad_page.html --max-pages 1 --fail-threshold 0
.\.venv\Scripts\python.exe -m agent_access.harness_cli --jobs examples/harness_jobs.json
.\.venv\Scripts\python.exe -m pytest -n 0 tests/test_harness.py
```

The first three commands run real AI audits and require an API key. The final command runs offline harness tests using simulated audit results plus real worker termination checks. It does not audit a website with AI. The included bad-page fixture intentionally contains accessibility defects, so a real run can exit 1 with `--fail-threshold 0` even when execution succeeds.

Use `--help` for model, page, budget and timeout options. Default execution is headless with an emulated screen reader, same-site exploration, blocked writes, disabled trace uploads and memory disabled. `--no-interaction` disables agent clicking and typing. The reviewer remains enabled. This version intentionally exposes no write-enabling option.

The model-call budget must exceed the auditor's 60-call closing reserve. `--budget-minutes` stops new working-agent activity; `--timeout-seconds` terminates the whole worker and its process tree. Cleanup can take additional time beyond that deadline. No automatic retries occur. Ctrl+C records cancellation where possible and exits 130.

## Results

Each CLI invocation creates a unique batch directory beneath `reports/harness`. Every job has its own unique directory with `manifest.json`, `stdout.log`, `stderr.log`, and the existing auditor's nested `report.json`, `report.md` and screenshots. The batch directory contains `batch-summary.json`. Timestamps use UTC. Logs and reports may contain website content; keep output in an appropriate location.

Execution status is `completed`, `failed`, `timed_out` or `cancelled`. Reports with execution errors are classified as failed even if they contain partial findings. A completed audit can contain accessibility failures, needs-review results or untested criteria.

Exit codes: **0** completed within the configured failure threshold; **1** completed but target-level failing criteria exceeded `--fail-threshold`; **2** invalid CLI/configuration; **3** execution failure or timeout; **130** interrupted. Without a threshold, accessibility findings alone do not change the exit code. In batches, execution failure takes precedence over findings.

JSON batches are arrays of configuration objects matching `HarnessConfig`. Unknown fields and invalid jobs are rejected before any audit begins. Put audit options in the file when using `--jobs`; `--output-dir` controls the whole batch. Relative HTML paths resolve from the JSON file's directory. Jobs run sequentially and continue after execution failures.

## Library

Save this as `run_harness.py` in the project root and run `.\.venv\Scripts\python.exe run_harness.py`. The existing auditor loads the project `.env` inside its worker.

```python
import asyncio
from agent_access.harness import HarnessConfig, run_job

async def main():
    result = await run_job(HarnessConfig(
        url="https://example.com",
        max_pages=1,
        timeout_seconds=3900,
        fail_threshold=0,
    ))
    print(result.status, result.artifacts)

if __name__ == "__main__":
    asyncio.run(main())
```

This is an automated accessibility assessment, not proof of full WCAG conformance. Emulated screen-reader checks do not replace real assistive-technology testing and human review. A timed-out worker may leave partial files; its manifest does not mark them as a completed audit.
