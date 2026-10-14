"""CLI entry point: python -m agent_access.harness_cli URL or --jobs FILE."""
from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, replace
from pathlib import Path

from agent_access.harness import HarnessConfig, run_batch


def load_jobs(path: Path, output_dir: Path) -> list[HarnessConfig]:
    values = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(values, list) or not values:
        raise ValueError('Jobs file must be a nonempty JSON array')
    configs = []
    for item in values:
        if not isinstance(item, dict):
            raise ValueError('Each job must be a JSON object')
        item = dict(item)
        item['output_dir'] = output_dir
        if item.get('html_file'):
            item['html_file'] = path.resolve().parent / item['html_file']
        configs.append(HarnessConfig.from_dict(item))
    return configs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Run isolated AI website accessibility audits')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('url', nargs='?')
    source.add_argument('--html-file', type=Path)
    source.add_argument('--jobs', type=Path, help='JSON array of job configurations')
    parser.add_argument('--output-dir', type=Path, default=Path('reports/harness'))
    parser.add_argument('--level', choices=['A', 'AA', 'AAA'], default='AA')
    parser.add_argument('--pages', type=int, default=1)
    parser.add_argument('--max-pages', type=int, default=10)
    parser.add_argument('--budget-calls', type=int, default=400)
    parser.add_argument('--budget-minutes', type=float, default=60)
    parser.add_argument('--timeout-seconds', type=float, default=3900)
    parser.add_argument('--model')
    parser.add_argument('--reviewer-model')
    parser.add_argument('--no-interaction', action='store_true')
    parser.add_argument('--fail-threshold', type=int, help='Exit 1 when target-level failing criteria exceed this count')
    args = parser.parse_args(argv)
    try:
        if args.jobs:
            # Per-job options come from JSON; reject flags that would otherwise be silently ignored.
            defaults = parser.parse_args(['--jobs', str(args.jobs)])
            for name in vars(args):
                if name not in ('jobs', 'output_dir') and getattr(args, name) != getattr(defaults, name):
                    raise ValueError('With --jobs, put audit options in the JSON file')
            configs = load_jobs(args.jobs, args.output_dir)
        else:
            values = vars(args).copy()
            values.pop('jobs')
            values['interaction'] = not values.pop('no_interaction')
            configs = [HarnessConfig.from_dict(values)]
        # Keep every batch summary in its own directory too.
        from datetime import datetime, timezone
        from uuid import uuid4
        batch_dir = args.output_dir.resolve() / f'batch-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex}'
        configs = [replace(config, output_dir=batch_dir) for config in configs]
        results = asyncio.run(run_batch(configs, batch_dir))
    except (ValueError, TypeError, OSError) as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        return 130
    for result in results:
        print(json.dumps(asdict(result), default=str))
    print(f'Batch summary: {batch_dir / "batch-summary.json"}')
    return max(result.exit_code for result in results)


if __name__ == '__main__':
    raise SystemExit(main())
