"""Sweep real websites without a model: do all probes and all 86 rules run, and what do they record?

    python -m evals.site_check URL [URL ...] [--level AAA] [--out reports/site_check]
    python -m evals.site_check --summarize [--out reports/site_check]

Per site it writes <out>/<host>[-<path>].json with probe errors, rule errors, rules that recorded only the
"no automated evidence" placeholder, per-status counts and timings. --summarize prints a table over every
JSON in <out>, so several terminals can each check their own sites and one summarises. No OpenAI calls.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

from agent_access.agent_tools.browser_tools import open_url
from agent_access.context import AuditContext
from agent_access.engine import run_sweep
from agent_access.probes import PROBE_ORDER
from agent_access.rules import TOOLS_IN_ORDER

PLACEHOLDER = "No automated evidence either way"


def _file_name(url: str) -> str:
    return re.sub(r"[^A-Za-z0-9.-]+", "-", re.sub(r"^https?://", "", url)).strip("-")[:80] + ".json"


async def check_site(url: str, level: str) -> dict:
    start = time.monotonic()
    async with AuditContext(target_level=level, screen_reader="emulated", tracing=False) as audit:
        loaded = await open_url(audit, url)
        if "error" in loaded:
            return {"url": url, "load_error": loaded["error"], "seconds": round(time.monotonic() - start, 1)}
        runs = await run_sweep(audit)
        page = audit.current_url
        probes = audit.observations[page]
        findings = audit.findings_for(page)
        by_rule: dict[str, list] = {}
        for f in findings:
            by_rule.setdefault(f.check_id, []).append(f)
        placeholder_only = sorted(name for name, fs in by_rule.items()
                                  if all(f.description.startswith(PLACEHOLDER) for f in fs))
        return {
            "url": url, "final_url": page, "title": loaded.get("title"), "level": level,
            "seconds": round(time.monotonic() - start, 1),
            "probes_run": len(probes), "probes_expected": len(PROBE_ORDER),
            "probe_errors": {n: r.error for n, r in probes.items() if r.error},
            "probe_empty": sorted(n for n, r in probes.items() if not r.error and not r.data),
            "rules_run": len(runs), "rules_expected": len(TOOLS_IN_ORDER),
            "rule_errors": {r["tool"]: r["error"] for r in runs if not r["ok"]},
            "placeholder_only": placeholder_only,
            "status_counts": dict(Counter(f.status.value for f in findings)),
            "rules_by_status": {s: sorted({f.check_id for f in findings if f.status.value == s})
                                for s in ("fail", "needs_review", "pass", "not_applicable")},
            "slowest_rules": sorted(((r["tool"], r["seconds"]) for r in runs), key=lambda x: -x[1])[:5],
            "screen_reader": audit.engines.get("screen reader"),
        }


def summarize(out: Path) -> None:
    results = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(out.glob("*.json"))]
    if not results:
        print(f"No results in {out}")
        return
    print("| site | s | probes | rules | probe errors | rule errors | placeholder-only | fail / review / pass / n.a. |")
    print("|---|---|---|---|---|---|---|---|")
    placeholder_counts: Counter = Counter()
    for r in results:
        if "load_error" in r:
            print(f"| {r['url']} | {r['seconds']} | LOAD FAILED: {r['load_error'][:60]} | | | | | |")
            continue
        c = r["status_counts"]
        placeholder_counts.update(r["placeholder_only"])
        print(f"| {r['url']} | {r['seconds']} | {r['probes_run']}/{r['probes_expected']} | {r['rules_run']}/{r['rules_expected']} "
              f"| {len(r['probe_errors'])} | {len(r['rule_errors'])} | {len(r['placeholder_only'])} "
              f"| {c.get('fail', 0)} / {c.get('needs_review', 0)} / {c.get('pass', 0)} / {c.get('not_applicable', 0)} |")
    ok = [r for r in results if "load_error" not in r]
    errors = {n: e for r in ok for n, e in {**r["probe_errors"], **r["rule_errors"]}.items()}
    print(f"\nSites checked: {len(results)}; loaded: {len(ok)}; sites with any probe/rule error: "
          f"{sum(1 for r in ok if r['probe_errors'] or r['rule_errors'])}")
    for name, err in errors.items():
        print(f"  error {name}: {err}")
    if placeholder_counts:
        print("\nRules that recorded only the placeholder (sites):")
        for name, n in placeholder_counts.most_common():
            print(f"  {name}: {n}/{len(ok)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("urls", nargs="*")
    parser.add_argument("--level", default="AAA", choices=["A", "AA", "AAA"])
    parser.add_argument("--out", default="reports/site_check")
    parser.add_argument("--summarize", action="store_true")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.summarize:
        summarize(out)
        return 0
    failed = 0
    for url in args.urls:
        try:
            result = asyncio.run(check_site(url, args.level))
        except Exception as exc:  # the next site must still run
            result = {"url": url, "load_error": f"{type(exc).__name__}: {str(exc)[:200]}", "seconds": 0}
        (out / _file_name(url)).write_text(json.dumps(result, indent=2), encoding="utf-8")
        problems = (len(result.get("probe_errors", {})) + len(result.get("rule_errors", {}))
                    + ("load_error" in result))
        failed += bool(problems)
        print(f"{url}: {result['seconds']}s, probe errors {len(result.get('probe_errors', {}))}, "
              f"rule errors {len(result.get('rule_errors', {}))}, placeholder-only {len(result.get('placeholder_only', []))}"
              + (f", LOAD FAILED: {result['load_error']}" if "load_error" in result else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
