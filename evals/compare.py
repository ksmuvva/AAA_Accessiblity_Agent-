"""Compare two eval results files (from evals/run_eval.py), e.g. main against a branch run with the same flags.

    python -m evals.compare evals/results/eval-A.json evals/results/eval-B.json

Prints each run's settings and metrics, the change in each metric, the model calls per case where both runs recorded
them, and every criterion whose outcome changed. No model calls.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

METRICS = ("recall", "precision", "decided", "false_passes")
SETTINGS = ("git_commit", "mode", "measurement", "model", "reviewer_model", "used_lessons")


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare(a: dict, b: dict) -> list[str]:
    lines = ["            " + "  ".join(f"{k:>14}" for k in ("A", "B"))]
    lines += [f"{k:>12}" + "  ".join(f"{str(run.get(k)):>14}" for run in (a, b)) for k in SETTINGS]
    lines.append("")
    for k in METRICS:
        x, y = a["metrics"].get(k), b["metrics"].get(k)
        delta = f"{y - x:+.3f}" if isinstance(x, (int, float)) and isinstance(y, (int, float)) else "n/a"
        lines.append(f"{k:>12}{str(x):>16}{str(y):>16}   change {delta}")

    cases_a = {c["name"]: c for c in a["cases"]}
    cases_b = {c["name"]: c for c in b["cases"]}
    shared = [n for n in cases_a if n in cases_b]
    calls = [(n, cases_a[n].get("model_calls"), cases_b[n].get("model_calls")) for n in shared]
    if any(x is not None and y is not None for _, x, y in calls):
        lines += ["", "Model calls per case (A -> B):"]
        lines += [f"  {n}: {x} -> {y}" for n, x, y in calls if x is not None and y is not None]

    changed = []
    for n in shared:
        rows_a = {r["criterion"]: r["outcome"] for r in cases_a[n]["rows"]}
        rows_b = {r["criterion"]: r["outcome"] for r in cases_b[n]["rows"]}
        changed += [f"  {n} {c}: {rows_a[c]} -> {rows_b[c]}" for c in sorted(rows_a) if c in rows_b and rows_a[c] != rows_b[c]]
    lines += ["", "Outcomes that changed:"] + (changed or ["  none"])
    only = sorted(set(cases_a) ^ set(cases_b))
    if only:
        lines += ["", "Cases in only one run (not compared): " + ", ".join(only)]
    return lines


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    print("\n".join(compare(load(args[0]), load(args[1]))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
