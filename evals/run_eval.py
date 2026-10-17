"""Measure how well the agent team judges what automation can't decide, against pages with known answers.

Runs the full team (real model, emulated screen reader, headless) on every case in evals/cases.json and scores
each expected criterion. This makes OpenAI calls and costs money; the unit tests never run it.

    python -m evals.run_eval                       # all cases, model from $OPENAI_MODEL
    python -m evals.run_eval --model gpt-5 --reviewer-model o4-mini --cases judgement_bad
    python -m evals.run_eval --fail-on-false-pass  # exit 1 if any defect was reported as a pass (for CI)
    python -m evals.run_eval --use-lessons         # let the team read past lessons (measures what learning adds)
    python -m evals.run_eval --mode agent --measurement lazy   # the CLI's defaults: the planner owns the run

Comparing two versions (e.g. main and a branch): run the same cases with the same flags on each checkout, then
    python -m evals.compare evals/results/eval-A.json evals/results/eval-B.json
Each results file records the git commit, mode, measurement and the model calls each case used.

Learning: every false pass and false fail becomes a global lesson in the memory store (--memory, default
reports/memory.sqlite, the store audits read), with what the team said and, if the case has "notes":
{criterion: text}, the truth. Scoring runs without lessons by default, so the metrics measure the model and
prompts, not answers learned from these same cases; --use-lessons scores with them. --memory off writes nothing.

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


def metrics(totals: Counter) -> dict:
    """Recall: defects reported as failures. Precision: reported failures that are real. Decided: share of
    criteria the team settled (pass or fail) instead of leaving to a human."""
    caught, false_fail = totals["caught"], totals["false_fail"]
    expected_fails = caught + totals["false_pass"] + totals["flagged_for_human"]
    scored = sum(totals[o] for o in OUTCOMES)
    decided = caught + false_fail + totals["false_pass"] + totals["correct_pass"]
    return {
        "recall": round(caught / expected_fails, 3) if expected_fails else None,
        "precision": round(caught / (caught + false_fail), 3) if caught + false_fail else None,
        "decided": round(decided / scored, 3) if scored else None,
        "false_passes": totals["false_pass"],
    }


def git_commit() -> str | None:
    """The checked-out commit, so two results files can be told apart."""
    import subprocess

    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=HERE, capture_output=True, text=True,
                              timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def open_memory(args: argparse.Namespace):
    from agent_access.memory import Memory

    return None if args.memory == "off" else Memory.open(args.memory)


async def run_case(case: dict, args: argparse.Namespace, memory=None) -> dict:
    """Run and score one case. With `memory`, its false passes/fails become lessons (and, with --use-lessons, the
    team reads the lessons while it audits)."""
    from agent_access.team import run_audit
    from agent_access.context import AuditContext
    from agent_access.agent_tools.browser_tools import load_html_content
    from agent_access.memory import lessons_from_eval

    html = (HERE / case["file"]).read_text(encoding="utf-8")
    async with AuditContext(screen_reader="emulated", target_level=case.get("level", "AA"),
                            tracing=not args.no_trace, measurement=args.measurement) as audit:
        if memory is not None and args.use_lessons:
            audit.memory = memory
        await load_html_content(audit, html, f"file:{Path(case['file']).name}")
        outcome = await run_audit(audit, model=args.model, reviewer_model=args.reviewer_model, mode=args.mode,
                                  review=not args.skip_review, progress=lambda m: print(f"  {m}", file=sys.stderr))
    criteria = (outcome.report.criteria + outcome.report.beyond_target) if outcome.report else []
    actual = {c.wcag_criterion: c.status.value for c in criteria}
    result = {"name": case["name"], **score(case["expected"], actual), "errors": outcome.errors,
              "warnings": outcome.warnings, "model_calls": (outcome.budget_used or {}).get("model_calls")}
    result["lessons_written"] = lessons_from_eval(memory, result, outcome.report, case) if memory is not None else 0
    return result


async def main_async(args: argparse.Namespace) -> int:
    spec = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    cases = [c for c in spec["cases"] if not args.cases or c["name"] in args.cases]
    if not cases:
        print("No matching cases.", file=sys.stderr)
        return 2
    results, totals = [], Counter()
    memory = open_memory(args)
    for case in cases:
        print(f"Case {case['name']} ...", file=sys.stderr)
        result = await run_case(case, args, memory)
        results.append(result)
        totals.update(result["totals"])
        for r in result["rows"]:
            mark = "" if r["outcome"] in ("caught", "correct_pass") else "   <--"
            print(f"  {r['criterion']:7} expected {r['expected']:5} got {r['actual']:15} {r['outcome']}{mark}")
    summary = metrics(totals)
    print(f"\nRecall {summary['recall']}  Precision {summary['precision']}  Decided {summary['decided']}  "
          f"False passes {summary['false_passes']}")
    lessons = sum(r["lessons_written"] for r in results)
    if memory is not None:
        print(f"Lessons written to {memory.path}: {lessons}" + ("  (scored WITH past lessons)" if args.use_lessons else ""))

    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"eval-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"git_commit": git_commit(), "mode": args.mode, "measurement": args.measurement,
                               "model": args.model or os.environ.get("OPENAI_MODEL"), "reviewer_model": args.reviewer_model,
                               "metrics": summary, "totals": dict(totals), "cases": results,
                               "memory": args.memory, "used_lessons": args.use_lessons, "lessons_written": lessons},
                              indent=2), encoding="utf-8")
    print(f"Results: {out}")
    return 1 if args.fail_on_false_pass and summary["false_passes"] else 0


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description="Score the agent team's judgements against pages with known answers.")
    parser.add_argument("--cases", nargs="*", help="case names to run (default: all)")
    parser.add_argument("--model", help="model for the auditors (default: $OPENAI_MODEL)")
    parser.add_argument("--reviewer-model", help="model for the reviewer (default: $OPENAI_REVIEWER_MODEL, else --model)")
    parser.add_argument("--skip-review", action="store_true", help="score without the reviewer")
    parser.add_argument("--no-trace", action="store_true", help="don't send traces to OpenAI")
    parser.add_argument("--fail-on-false-pass", action="store_true", help="exit 1 if any defect was reported as a pass")
    parser.add_argument("--memory", default=str(Path("reports") / "memory.sqlite"),
                        help="memory store that false passes/fails are written to as lessons, or 'off'")
    parser.add_argument("--use-lessons", action="store_true",
                        help="let the team read past lessons while it audits (default: score without them)")
    parser.add_argument("--mode", choices=["code", "agent"], default="code",
                        help="code (default): the fixed per-page flow; agent: the planner owns the run (the CLI default)")
    parser.add_argument("--measurement", choices=["eager", "lazy"], default="eager",
                        help="eager (default): every check runs on load; lazy: the agents choose (the CLI default)")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv()
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set. Add it to .env or your environment.", file=sys.stderr)
        return 2
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
