import asyncio
import json
import sys
from functools import wraps
from pathlib import Path

import pytest

from agent_access.harness import HarnessConfig, run_job, run_batch, run_process


def run_async(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return wrapped


@pytest.mark.parametrize('overrides', [
    {'url': 'file:///etc/passwd'}, {'url': 'https://user:password@example.com'},
    {'timeout_seconds': 0}, {'timeout_seconds': float('nan')},
    {'pages': 0}, {'pages': True}, {'max_pages': 1, 'pages': 2},
    {'budget_calls': 60}, {'level': 'B'}, {'unexpected': 'field'},
])
def test_invalid_config(overrides):
    with pytest.raises((ValueError, TypeError)):
        HarnessConfig.from_dict({'url': 'https://example.com', **overrides})


async def fake_audit(command, directory, timeout):
    report_dir = directory / 'audit'
    report_dir.mkdir()
    (report_dir / 'report.json').write_text(json.dumps({
        'report': {'criteria': [{'status': 'fail'}, {'status': 'needs_review'}]},
        'errors': [],
    }))
    (report_dir / 'report.md').write_text('Audit report')
    return 0


@run_async
async def test_reports_manifest_and_unique_directories(tmp_path):
    config = HarnessConfig(url='https://example.com', output_dir=tmp_path, fail_threshold=0)
    first = await run_job(config, process_runner=fake_audit)
    second = await run_job(config, process_runner=fake_audit)
    assert first.status == 'completed'
    assert first.exit_code == 1
    assert first.counts == {'fail': 1, 'needs_review': 1}
    assert first.output_dir != second.output_dir
    manifest = json.loads((first.output_dir / 'manifest.json').read_text())
    assert manifest['finished_at'].endswith('+00:00')
    assert Path(manifest['artifacts']['report_json']).is_file()


@run_async
async def test_batch_continues_after_failure(tmp_path):
    calls = 0

    async def runner(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError('worker failed')
        return await fake_audit(*args)

    configs = [HarnessConfig(url='https://example.com', output_dir=tmp_path)] * 2
    results = await run_batch(configs, tmp_path, process_runner=runner)
    assert [r.status for r in results] == ['failed', 'completed']
    assert (tmp_path / 'batch-summary.json').is_file()


@run_async
async def test_a_cancelled_batch_lists_every_job(tmp_path):
    async def second_is_cancelled(*args):
        if len(list(tmp_path.glob('*/audit'))) == 1:
            raise asyncio.CancelledError
        return await fake_audit(*args)

    with pytest.raises(asyncio.CancelledError):
        await run_batch([HarnessConfig(url='https://example.com', output_dir=tmp_path)] * 3, tmp_path,
                        process_runner=second_is_cancelled)
    summary = json.loads((tmp_path / 'batch-summary.json').read_text())
    assert [job['status'] for job in summary] == ['completed', 'cancelled', 'not_run']


@run_async
async def test_timeout_stops_real_worker(tmp_path):
    with pytest.raises(TimeoutError):
        await run_process([sys.executable, '-c', 'import time; time.sleep(30)'], tmp_path, .2)
    assert (tmp_path / 'stdout.log').is_file()


@run_async
async def test_cancel_records_manifest_and_propagates(tmp_path):
    async def cancelled(*args):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_job(HarnessConfig(url='https://example.com', output_dir=tmp_path),
                      process_runner=cancelled)
    manifest = json.loads(next(tmp_path.glob('*/manifest.json')).read_text())
    assert manifest['status'] == 'cancelled'


def test_batch_prevalidation_and_relative_fixture(tmp_path):
    from agent_access.harness_cli import load_jobs
    fixture = tmp_path / 'page.html'
    fixture.write_text('<html lang="en"></html>')
    jobs = tmp_path / 'jobs.json'
    jobs.write_text(json.dumps([{'html_file': 'page.html'}]))
    assert load_jobs(jobs, tmp_path)[0].html_file == fixture
    jobs.write_text(json.dumps([{'url': 'https://example.com'}, {'pages': 0}]))
    with pytest.raises(ValueError):
        load_jobs(jobs, tmp_path)


@run_async
async def test_timeout_manifest(tmp_path):
    async def expired(*args):
        raise TimeoutError
    result = await run_job(HarnessConfig(url='https://example.com', output_dir=tmp_path),
                           process_runner=expired)
    assert result.status == 'timed_out'
    assert result.exit_code == 3


@run_async
async def test_worker_failure_preserves_partial_report(tmp_path):
    async def partial(*args):
        await fake_audit(*args)
        return 1
    result = await run_job(HarnessConfig(url='https://example.com', output_dir=tmp_path),
                           process_runner=partial)
    assert result.status == 'failed'
    assert Path(result.artifacts['report_json']).is_file()


@run_async
async def test_a_stopped_worker_is_incomplete_and_its_reason_is_kept(tmp_path):
    async def stopped(command, directory, timeout):
        await fake_audit(command, directory, timeout)
        (directory / 'stderr.log').write_text('Traceback ...\nSIGTERM: stopped by the runner\n')
        return 4
    result = await run_job(HarnessConfig(url='https://example.com', output_dir=tmp_path), process_runner=stopped)
    assert result.status == 'incomplete' and result.exit_code == 3 and 'report_json' in result.artifacts
    assert result.error.endswith('(stderr: SIGTERM: stopped by the runner)')


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX signals')
@run_async
async def test_timeout_asks_the_worker_to_stop_before_killing_it(tmp_path):
    marker = tmp_path / 'terminated'
    script = (f"import pathlib, signal, sys, time\nsignal.signal(signal.SIGTERM, lambda *a: "
              f"(pathlib.Path({str(marker)!r}).write_text('partial report'), sys.exit(4)))\ntime.sleep(30)")
    with pytest.raises(TimeoutError):
        await run_process([sys.executable, '-c', script], tmp_path, 1)
    assert marker.read_text() == 'partial report'


def test_worker_command_keeps_review_and_safe_defaults(tmp_path):
    command = HarnessConfig(url='https://example.com').command(tmp_path)
    assert '--skip-review' not in command
    assert '--allow-submit' not in command
    assert command[command.index('--memory') + 1] == 'off'
    assert command[command.index('--screen-reader') + 1] == 'emulated'
    assert '--no-trace' in command


@pytest.mark.parametrize('exit_code', [0, 1, 3])
def test_cli_propagates_outcome(monkeypatch, tmp_path, exit_code):
    from agent_access import harness_cli
    from agent_access.harness import HarnessResult

    async def batch(configs, output_dir):
        assert configs[0].url == 'https://example.com'
        return [HarnessResult(output_dir=output_dir, exit_code=exit_code)]

    monkeypatch.setattr(harness_cli, 'run_batch', batch)
    assert harness_cli.main(['https://example.com', '--output-dir', str(tmp_path)]) == exit_code


def test_cli_invalid_batch_never_starts_worker(monkeypatch, tmp_path):
    from agent_access import harness_cli
    jobs = tmp_path / 'jobs.json'
    jobs.write_text('[{"url":"https://example.com"},{"url":"file:///bad"}]')

    def forbidden(*args):
        pytest.fail('Invalid batch started a worker')

    monkeypatch.setattr(harness_cli, 'run_batch', forbidden)
    with pytest.raises(SystemExit) as error:
        harness_cli.main(['--jobs', str(jobs)])
    assert error.value.code == 2


@run_async
async def test_cancellation_terminates_worker(tmp_path):
    task = asyncio.create_task(run_process(
        [sys.executable, '-c', 'import time; time.sleep(30)'], tmp_path, 60))
    await asyncio.sleep(.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (tmp_path / 'stderr.log').is_file()
