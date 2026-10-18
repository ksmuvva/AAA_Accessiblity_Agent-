"""Measure how well the agent team judges what automation can't decide, against pages with known answers.

Runs the full team (real model, emulated screen reader, headless) on every case in evals/cases.json and scores
each expected criterion. This makes OpenAI calls and costs money; the unit tests never run it.

    python -m evals.run_eval                       # all cases, model from $OPENAI_MODEL
    python -m evals.run_eval --model gpt-5 --reviewer-model o4-mini --cases judgement_bad
    python -m evals.run_eval --fail-on-false-pass  # exit 1 if any defect was reported as a pass (for CI)
    python -m evals.run_eval --use-lessons         # let the team read past lessons (measures what learning adds)
    python -m evals.run_eval --mode agent --measurement lazy   # the CLI's defaults: the planner owns the run
    python -m evals.run_eval --mode baseline       # the code alone, no model and no key: what the agents must beat
    python -m evals.run_eval --repeats 5           # each case 5 times: one run decides nothing (see the intervals)

Comparing two versions (e.g. main and a branch): run the same cases with the same flags on each checkout, then
    python -m evals.compare evals/results/eval-A.json evals/results/eval-B.json
Each results file records the git commit, mode, measurement and the model calls each case used.

Learning: every false pass and false fail becomes a global lesson in the memory store (--memory, default
reports/eval_memory.sqlite, apart from the audits' reports/memory.sqlite so answers can't leak into real audits),
with what the team said and, if the case has "notes": {criterion: text}, the truth. Scoring runs without lessons by default, so the metrics measure the model and
prompts, not answers learned from these same cases; --use-lessons scores with them. --memory off writes nothing.

Exit codes: 0 done; 1 a false pass with --fail-on-false-pass; 2 usage; 3 a run that can't be scored (a case errored or
produced no report, or the team decided nothing at all, which would otherwise look like a run without false passes).

Outcomes per criterion:
    caught             expected fail, reported fail
    false_pass         expected fail, reported pass or not_applicable   (the worst error for an auditor)
    flagged_for_human  expected fail, reported needs_review or not_tested
    correct_pass       expected pass, reported pass
    false_fail         expected pass, reported fail
    left_for_human     expected pass, reported needs_review, not_tested, or not_applicable
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
OUTCOMES = ("caught", "false_pass", "flagged_for_human", "correct_pass", "false_fail", "left_for_human")


def classify(expected: str, actual: str) -> str:
    if expected == "fail":
        return {"fail": "caught", "pass": "false_pass", "not_applicable": "false_pass"}.get(actual, "flagged_for_human")
    return {"pass": "correct_pass", "fail": "false_fail"}.get(actual, "left_for_human")


def score(expected: dict[str, str], actual: dict[str, str]) -> dict:
    """Score one case: per-criterion outcomes plus totals."""
    rows = [{"criterion": c, "expected": e, "actual": actual.get(c, "not_tested"),
             "outcome": classify(e, actual.get(c, "not_tested"))} for c, e in sorted(expected.items())]
    return {"rows": rows, "totals": dict(Counter(r["outcome"] for r in rows))}


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    """95% interval for k of n (Wilson): honest at 0 of n, unlike k/n +- a normal margin."""
    if not n:
        return None
    p, d = k / n, 1 + z * z / n
    centre, half = (p + z * z / (2 * n)) / d, z * ((p * (1 - p) + z * z / (4 * n)) / n) ** 0.5 / d
    return [round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)]


def metrics(totals: Counter) -> dict:
    """Recall: defects reported as failures (needs_review is never a catch). Precision: reported failures that are
    real. Decided: share of criteria the team settled (pass or fail) instead of leaving to a human. Each rate has its
    95% interval: with few items one run can't tell a change from noise."""
    caught, false_fail, false_pass = totals["caught"], totals["false_fail"], totals["false_pass"]
    expected_fails = caught + false_pass + totals["flagged_for_human"]
    scored = sum(totals[o] for o in OUTCOMES)
    decided = caught + false_fail + false_pass + totals["correct_pass"]
    return {
        "recall": round(caught / expected_fails, 3) if expected_fails else None,
        "recall_ci": wilson(caught, expected_fails),
        "precision": round(caught / (caught + false_fail), 3) if caught + false_fail else None,
        "decided": round(decided / scored, 3) if scored else None,
        "false_passes": false_pass,
        "false_pass_rate_ci": wilson(false_pass, expected_fails),
        "expected_fails": expected_fails,
    }


def git_commit() -> str | None:
    """The checked-out commit ("+dirty" with uncommitted changes), so two results files can be told apart."""
    import subprocess

    def git(*args) -> str:
        return subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True, timeout=10).stdout.strip()
    try:
        return (git("rev-parse", "--short", "HEAD") + ("+dirty" if git("status", "--porcelain", "--", ".") else "")) or None
    except (OSError, subprocess.SubprocessError):
        return None


def versions() -> dict:
    from importlib.metadata import version

    return {"python": sys.version.split()[0], **{name: version(name) for name in ("openai-agents", "openai", "playwright")}}


def open_memory(args: argparse.Namespace):
    from agent_access.memory import Memory

    return None if args.memory == "off" else Memory.open(args.memory)


async def run_case(case: dict, args: argparse.Namespace, memory=None) -> dict:
    """Run and score one case. With `memory`, its false passes/fails become lessons (and, with --use-lessons, the
    team reads the lessons while it audits)."""
    from agent_access.team import run_audit
    from agent_access.team.orchestrator import AuditOutcome
    from agent_access.agent_tools.report_tools import compile_site_report
    from agent_access.engine import run_sweep
    from agent_access.context import AuditContext
    from agent_access.agent_tools.browser_tools import load_html_content
    from agent_access.memory import lessons_from_eval
    from agent_access.budget import Budget

    html = (HERE / case["file"]).read_text(encoding="utf-8")
    # Interaction is on unless --no-interaction, as in the CLI: a case that needs it (a form behind a button) can't be
    # passed without it.
    async with AuditContext(screen_reader="emulated", target_level=case.get("level", "AA"), tracing=args.trace,
                            measurement=args.measurement, allow_interaction=not args.no_interaction) as audit:
        if memory is not None and args.use_lessons:
            audit.memory = memory
        if args.mode == "code":   # count model calls without limiting them (agent mode brings its own Budget)
            audit.budget = Budget(max_model_calls=10**9, reserve_calls=0, max_minutes=10**9)
        await load_html_content(audit, html, "file:page.html")   # the case's file name would give the answers away
        if args.mode == "baseline":
            await run_sweep(audit)
            outcome = AuditOutcome(report=compile_site_report(audit))
        else:
            outcome = await run_audit(audit, model=args.model, reviewer_model=args.reviewer_model, mode=args.mode,
                                      review=not args.skip_review, progress=lambda m: print(f"  {m}", file=sys.stderr))
        model_calls = getattr(audit.budget, "model_calls", None)
        models = audit.engines.get("Agent models")
    criteria = (outcome.report.criteria + outcome.report.beyond_target) if outcome.report else []
    actual = {c.wcag_criterion: c.status.value for c in criteria}
    result = {"name": case["name"], **score(case["expected"], actual), "errors": outcome.errors,
              "warnings": outcome.warnings, "model_calls": model_calls, "models": models,
              "reported": outcome.report is not None}
    result["lessons_written"] = lessons_from_eval(memory, result, outcome.report, case) if memory is not None else 0
    return result


async def main_async(args: argparse.Namespace) -> int:
    spec = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    cases = [c for c in spec["cases"] if not args.cases or c["name"] in args.cases]
    if not cases:
        print("No matching cases.", file=sys.stderr)
        return 2
    results, totals = [], Counter()
    memory = None if args.mode == "baseline" else open_memory(args)
    for case in [c for c in cases for _ in range(args.repeats)]:
        print(f"Case {case['name']} ...", file=sys.stderr)
        result = await run_case(case, args, memory)
        results.append(result)
        totals.update(result["totals"])
        for r in result["rows"]:
            mark = "" if r["outcome"] in ("caught", "correct_pass") else "   <--"
            print(f"  {r['criterion']:7} expected {r['expected']:5} got {r['actual']:15} {r['outcome']}{mark}")
    summary = metrics(totals)
    print(f"\nRecall {summary['recall']} (95% {summary['recall_ci']})  Precision {summary['precision']}  "
          f"Decided {summary['decided']}  False passes {summary['false_passes']} (rate 95% {summary['false_pass_rate_ci']})")
    lessons = sum(r["lessons_written"] for r in results)
    if memory is not None:
        print(f"Lessons written to {memory.path}: {lessons}" + ("  (scored WITH past lessons)" if args.use_lessons else ""))

    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"eval-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"git_commit": git_commit(), "versions": versions(), "repeats": args.repeats,
                               "mode": args.mode, "measurement": args.measurement, "interaction": not args.no_interaction,
                               "model": args.model or os.environ.get("OPENAI_MODEL"), "reviewer_model": args.reviewer_model,
                               "metrics": summary, "totals": dict(totals), "cases": results,
                               "memory": args.memory, "used_lessons": args.use_lessons, "lessons_written": lessons},
                              indent=2), encoding="utf-8")
    print(f"Results: {out}")
    broken = [r["name"] for r in results if r["errors"] or not r["reported"]]
    if broken or not summary["decided"]:
        print(f"Not scorable: {', '.join(broken) or 'the team decided nothing'}.", file=sys.stderr)
        return 3
    return 1 if args.fail_on_false_pass and summary["false_passes"] else 0


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description="Score the agent team's judgements against pages with known answers.")
    parser.add_argument("--cases", nargs="*", help="case names to run (default: all)")
    parser.add_argument("--model", help="model for the auditors (default: $OPENAI_MODEL)")
    parser.add_argument("--reviewer-model", help="model for the reviewer (default: $OPENAI_REVIEWER_MODEL, else --model)")
    parser.add_argument("--skip-review", action="store_true", help="score without the reviewer")
    parser.add_argument("--trace", action="store_true", help="send traces to OpenAI")
    parser.add_argument("--fail-on-false-pass", action="store_true", help="exit 1 if any defect was reported as a pass")
    parser.add_argument("--memory", default=str(Path("reports") / "eval_memory.sqlite"),
                        help="memory store that false passes/fails are written to as lessons, or 'off'")
    parser.add_argument("--use-lessons", action="store_true",
                        help="let the team read past lessons while it audits (default: score without them)")
    parser.add_argument("--mode", choices=["code", "agent", "baseline"], default="code",
                        help="code (default): the fixed per-page flow; agent: the planner owns the run (the CLI "
                             "default); baseline: the automated checks alone, no model")
    parser.add_argument("--repeats", type=int, default=1, help="run each case this many times (default 1)")
    parser.add_argument("--measurement", choices=["eager", "lazy"], default="eager",
                        help="eager (default): every check runs on load; lazy: the agents choose (the CLI default)")
    parser.add_argument("--no-interaction", action="store_true",
                        help="forbid clicking, typing and key presses, as the CLI flag does (default: allowed, as in the "
                             "CLI); cases that need interaction are then scored without it")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv()
    if args.mode != "baseline" and not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set. Add it to .env or your environment.", file=sys.stderr)
        return 2
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
