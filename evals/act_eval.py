"""Score the measured rules on the W3C ACT rules' own examples, without a model.

    git clone --depth 1 https://github.com/act-rules/act-rules.github.io.git ../act-rules
    python -m evals.act_eval ../act-rules [--rules ID ...] [--out FILE]

Each ACT rule maps to WCAG criteria and gives passed, failed and inapplicable HTML examples. Every example is
loaded and the rules for its criteria are run. A failed example should fail the criterion ("caught"; a review is
"missed", a pass or not-applicable a "false pass", as in run_eval); a passed or inapplicable example must not fail
("false fail"). Examples that
load assets from the ACT site (/test-assets/...) run without them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from collections import Counter
from pathlib import Path

from agent_access.agent_tools.browser_tools import load_html_content
from agent_access.context import AuditContext
from agent_access.engine import run_rule
from agent_access.models import Status
from agent_access.rules import REGISTRY

HEADING = re.compile(r"^#### (Passed|Failed|Inapplicable) Example (\d+)", re.M)
HTML_BLOCK = re.compile(r"^```html\n(.*?)\n```", re.M | re.S)
# A criterion the rule tests for conformance; a "secondary" one (stricter or looser than the rule) is left out.
REQUIREMENT = re.compile(r"^\s+wcag2[0-2]:(\d\.\d\.\d+):.*\n((?:\s{4,}.*\n)*)", re.M)


def read_rule(path: Path) -> tuple[dict, list[tuple[str, str, str]]]:
    """An ACT rule file's id, name and WCAG criteria we have rules for, and its (outcome, label, html) examples."""
    text = path.read_text(encoding="utf-8")
    front = text.split("---", 2)[1]
    rule = {"id": re.search(r"^id: (\w+)", front, re.M).group(1), "name": re.search(r"^name: (.+)", front, re.M).group(1),
            "criteria": sorted({c for c, body in REQUIREMENT.findall(front) if "forConformance: true" in body and c in REGISTRY})}
    heads = list(HEADING.finditer(text))
    examples = []
    for head, after in zip(heads, heads[1:] + [None]):
        if block := HTML_BLOCK.search(text, head.end(), after.start() if after else len(text)):
            examples.append((head.group(1).lower(), f"{head.group(1)} {head.group(2)}", block.group(1)))
    return rule, examples


def score(outcome: str, statuses: set[Status]) -> str:
    if outcome == "failed":
        return "caught" if Status.FAIL in statuses else "missed" if not statuses or Status.NEEDS_REVIEW in statuses \
            else "false pass"
    return "false fail" if Status.FAIL in statuses else "ok"


async def run(repo: Path, only: list[str] | None) -> list[dict]:
    rows = []
    async with AuditContext(screen_reader="emulated") as audit:
        for path in sorted((repo / "_rules").glob("*.md")):
            rule, examples = read_rule(path)
            if not rule["criteria"] or only and rule["id"] not in only:
                continue
            for outcome, label, html in examples:
                try:
                    await load_html_content(audit, html, f"act:{rule['id']}:{label}")
                except Exception as exc:  # an example that navigates away at once (a refresh, a redirect) can't be loaded
                    rows += [{"rule": rule["id"], "name": rule["name"], "example": label, "criterion": c, "outcome": outcome,
                              "result": "error", "statuses": [], "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
                             for c in rule["criteria"]]
                    continue
                key = audit.current_url
                for criterion in rule["criteria"]:
                    await run_rule(audit, REGISTRY[criterion])
                    statuses = {f.status for f in audit.findings if f.page_url == key and f.wcag_criterion == criterion}
                    rows.append({"rule": rule["id"], "name": rule["name"], "example": label, "criterion": criterion,
                                 "outcome": outcome, "result": score(outcome, statuses),
                                 "statuses": sorted(s.value for s in statuses)})
                audit.observations.pop(key, None)  # keep memory flat over a thousand examples
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", type=Path, help="a clone of github.com/act-rules/act-rules.github.io")
    parser.add_argument("--rules", nargs="*", help="ACT rule ids to run (default: every rule mapped to a criterion)")
    parser.add_argument("--out", type=Path, help="results file (default evals/results/act-<timestamp>.json)")
    args = parser.parse_args()
    rows = asyncio.run(run(args.repo, args.rules))
    by_rule: dict[tuple, Counter] = {}
    for r in rows:
        by_rule.setdefault((r["rule"], r["criterion"], r["name"]), Counter())[r["result"]] += 1
    print(f"{'rule':8} {'crit':7} {'caught':>6} {'missed':>6} {'f.pass':>6} {'f.fail':>6} {'ok':>4}  name")
    for (rule, criterion, name), c in sorted(by_rule.items(), key=lambda kv: kv[0][1]):
        print(f"{rule:8} {criterion:7} {c['caught']:6} {c['missed']:6} {c['false pass']:6} {c['false fail']:6} {c['ok']:4}  {name[:60]}")
    total = Counter(r["result"] for r in rows)
    failed = total["caught"] + total["missed"] + total["false pass"]
    print(f"\n{len(rows)} checks: recall {total['caught'] / max(failed, 1):.2f} on failed examples, "
          f"{total['false pass']} false passes, {total['false fail']} false fails on "
          f"{total['false fail'] + total['ok']} passed or inapplicable examples; {total['error']} not loadable.")
    out = args.out or Path("evals/results") / f"act-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"totals": dict(total), "rows": rows}, indent=1), encoding="utf-8")
    print(f"Results: {out}")


if __name__ == "__main__":
    main()
