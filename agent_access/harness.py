"""Process-isolated execution harness for the existing accessibility auditor."""
from __future__ import annotations

import asyncio
import json
import math
import os
import signal
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from agent_access.budget import Budget

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STOP_GRACE_SECONDS = 15   # after SIGTERM, the worker writes its partial report


@dataclass(frozen=True)
class HarnessConfig:
    url: str | None = None
    html_file: Path | None = None
    output_dir: Path = Path('reports/harness')
    level: str = 'AA'
    pages: int = 1
    max_pages: int = 10
    budget_calls: int = 400
    budget_minutes: float = 60
    timeout_seconds: float = 3900
    model: str | None = None
    reviewer_model: str | None = None
    interaction: bool = True
    fail_threshold: int | None = None

    @classmethod
    def from_dict(cls, values: dict) -> HarnessConfig:
        if not isinstance(values, dict):
            raise ValueError('Each job must be a JSON object')
        config = cls(**values)
        config.validate()
        return config

    def validate(self) -> None:
        if bool(self.url) == bool(self.html_file):
            raise ValueError('Supply exactly one of url or html_file')
        if self.url:
            if not isinstance(self.url, str):
                raise ValueError('url must be a string')
            parsed = urlsplit(self.url)
            if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                    or parsed.username or parsed.password or any(c.isspace() for c in self.url)):
                raise ValueError('url must be HTTP(S), without embedded credentials or whitespace')
            _ = parsed.port
        if self.html_file and not Path(self.html_file).is_file():
            raise ValueError('html_file must exist')
        if self.level not in ('A', 'AA', 'AAA'):
            raise ValueError('level must be A, AA or AAA')
        for name in ('pages', 'max_pages', 'budget_calls'):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f'{name} must be a positive integer')
        if self.pages > self.max_pages or self.budget_calls <= Budget.reserve_calls:
            raise ValueError(f'max_pages must cover pages; budget_calls must exceed the {Budget.reserve_calls}-call reserve')
        if self.html_file and self.pages != 1:
            raise ValueError('Local HTML supports one page')
        for name in ('budget_minutes', 'timeout_seconds'):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be positive and finite')
        if type(self.interaction) is not bool:
            raise ValueError('interaction must be boolean')
        if self.fail_threshold is not None and (type(self.fail_threshold) is not int or self.fail_threshold < 0):
            raise ValueError('fail_threshold must be a nonnegative integer')
        for name in ('model', 'reviewer_model'):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip() or value.startswith('-')):
                raise ValueError(f'{name} must be a nonempty model name')
        Path(self.output_dir)

    def command(self, directory: Path) -> list[str]:
        command = [sys.executable, str(PROJECT_ROOT / 'main.py')]
        command += [self.url] if self.url else ['--html-file', str(Path(self.html_file).resolve())]
        command += ['--output-dir', str(directory), '--level', self.level,
                    '--pages', str(self.pages), '--max-pages', str(self.max_pages),
                    '--budget-calls', str(self.budget_calls), '--budget-minutes', str(self.budget_minutes),
                    '--mode', 'agent', '--screen-reader', 'emulated', '--memory', 'off', '--no-trace']
        if not self.interaction:
            command.append('--no-interaction')
        for flag, value in (('--model', self.model), ('--reviewer-model', self.reviewer_model)):
            if value:
                command += [flag, value]
        return command


@dataclass
class HarnessResult:
    output_dir: Path
    status: str = 'failed'
    exit_code: int = 3
    started_at: str = ''
    finished_at: str = ''
    duration_seconds: float = 0
    worker_exit_code: int | None = None
    counts: dict[str, int] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    error: str | None = None


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, default=str, allow_nan=False), encoding='utf-8')


async def stop_process(process) -> None:
    if os.name == 'nt':
        killer = await asyncio.create_subprocess_exec(
            'taskkill', '/PID', str(process.pid), '/T', '/F',
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await killer.wait()
    else:
        try:   # SIGTERM the CLI first (not its browser): it writes a partial report and exits 4
            os.kill(process.pid, signal.SIGTERM)
            await asyncio.wait_for(process.wait(), STOP_GRACE_SECONDS)
        except (ProcessLookupError, TimeoutError):
            pass
        try:   # then whatever is left of the process group
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    await process.wait()


async def run_process(command: list[str], directory: Path, timeout: float) -> int:
    """Deadline covers the worker; termination and cleanup can take additional time."""
    with (directory / 'stdout.log').open('wb') as stdout, (directory / 'stderr.log').open('wb') as stderr:
        process = await asyncio.create_subprocess_exec(
            *command, cwd=PROJECT_ROOT, stdout=stdout, stderr=stderr,
            **({'start_new_session': True} if os.name != 'nt' else {}))
        try:
            return await asyncio.wait_for(process.wait(), timeout)
        except (TimeoutError, asyncio.CancelledError):
            cleanup = asyncio.create_task(stop_process(process))
            await asyncio.shield(cleanup)
            raise


async def run_job(config: HarnessConfig, *, process_runner=run_process) -> HarnessResult:
    config.validate()
    directory = Path(config.output_dir).resolve() / f'{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex}'
    directory.mkdir(parents=True)
    result = HarnessResult(output_dir=directory, started_at=datetime.now(timezone.utc).isoformat())
    started = time.monotonic()
    try:
        result.worker_exit_code = await process_runner(config.command(directory), directory, config.timeout_seconds)
        reports = list(directory.glob('*/report.json'))
        if len(reports) != 1:
            raise ValueError('Worker did not produce exactly one audit report')
        payload = json.loads(reports[0].read_text(encoding='utf-8'))
        result.counts = dict(Counter(item['status'] for item in payload['report']['criteria']))
        result.artifacts['report_json'] = str(reports[0])
        markdown = reports[0].with_suffix('.md')
        if markdown.exists():
            result.artifacts['report_markdown'] = str(markdown)
        if result.worker_exit_code != 0 or payload.get('errors'):
            if result.worker_exit_code == 4:
                result.status = 'incomplete'   # stopped early (SIGTERM): the report it wrote is partial
            raise ValueError('Audit reported execution errors; inspect the report and stderr.log')
        result.status = 'completed'
        result.exit_code = int(config.fail_threshold is not None
                               and result.counts.get('fail', 0) > config.fail_threshold)
    except TimeoutError:
        result.status = 'timed_out'
        result.error = f'Audit exceeded {config.timeout_seconds:g} seconds'
    except asyncio.CancelledError:
        result.status = 'cancelled'
        result.error = 'Audit cancelled'
        raise
    except Exception as exc:
        stderr = directory / 'stderr.log'
        tail = stderr.read_text(encoding='utf-8', errors='replace').strip().splitlines()[-1:] if stderr.exists() else []
        result.error = f'{type(exc).__name__}: {exc}' + (f' (stderr: {tail[0][:300]})' if tail else '')
    finally:
        for report in directory.glob('*/report.json'):   # a timed-out worker's partial report too
            result.artifacts.setdefault('report_json', str(report))
        result.finished_at = datetime.now(timezone.utc).isoformat()
        result.duration_seconds = round(time.monotonic() - started, 3)
        for name in ('stdout', 'stderr'):
            log = directory / f'{name}.log'
            if log.exists():
                result.artifacts[name] = str(log)
        write_json(directory / 'manifest.json', {**asdict(result), 'config': asdict(config)})
    return result


async def run_batch(configs: list[HarnessConfig], output_dir: Path, *, process_runner=run_process) -> list[HarnessResult]:
    if not configs:
        raise ValueError('Batch must contain at least one job')
    for config in configs:
        config.validate()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    try:
        for config in configs:
            results.append(await run_job(config, process_runner=process_runner))
            write_json(output_dir / 'batch-summary.json', [asdict(result) for result in results])
    except asyncio.CancelledError:   # the summary still lists the job that was stopped and the ones never started
        write_json(output_dir / 'batch-summary.json', [asdict(r) for r in results] + [
            {'status': 'cancelled' if i == 0 else 'not_run', 'config': asdict(c)} for i, c in enumerate(configs[len(results):])])
        raise
    return results
