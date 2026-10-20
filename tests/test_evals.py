"""The eval harness's scoring and cases, without running a model."""

import json
import re
from collections import Counter

from evals.run_eval import HERE, metrics, score, wilson
from agent_access.catalogue.catalogue import load_catalogue


def test_score_and_metrics():
    expected = {"1.1.1": "fail", "2.4.2": "fail", "3.1.2": "fail", "2.4.6": "pass", "1.4.1": "pass"}
    actual = {"1.1.1": "fail", "2.4.2": "pass", "3.1.2": "needs_review", "2.4.6": "fail", "1.4.1": "pass"}
    result = score(expected, actual)
    assert {r["criterion"]: r["outcome"] for r in result["rows"]} == {
        "1.1.1": "caught", "2.4.2": "false_pass", "3.1.2": "flagged_for_human", "2.4.6": "false_fail",
        "1.4.1": "correct_pass"}
    m = metrics(Counter(result["totals"]))
    assert {k: m[k] for k in ("recall", "precision", "decided", "false_passes")} == {
        "recall": round(1 / 3, 3), "precision": 0.5, "decided": 0.8, "false_passes": 1}
    assert m["recall_ci"] == wilson(1, 3) and m["false_pass_rate_ci"] == wilson(1, 3)
    assert wilson(8, 14) == [0.326, 0.786] and wilson(0, 14) == [0.0, 0.215]   # 0 of 14 still allows 21.5%
    assert score({"1.2.1": "fail"}, {})["rows"][0]["outcome"] == "flagged_for_human"  # missing = not_tested


def test_cases_are_well_formed():
    catalogue = load_catalogue()
    for case in json.loads((HERE / "cases.json").read_text(encoding="utf-8"))["cases"]:
        assert (HERE / case["file"]).is_file(), case["file"]
        for criterion, status in case["expected"].items():
            assert catalogue.get(criterion) is not None and status in ("pass", "fail"), (case["name"], criterion)


def test_case_pages_carry_no_answers():
    """An agent can read the whole page (inspect_element returns its HTML, evaluate_js anything): comments, criterion
    numbers and verdict words in it would hand over the answers. Explanations live in cases.json notes."""
    for path in (HERE / "cases").rglob("*.html"):
        html = path.read_text(encoding="utf-8")
        assert "<!--" not in html and "/*" not in html, path.name
        assert not re.search(r"\b[1-4]\.[1-5]\.\d{1,2}\b", html), path.name
        attributes = " ".join(re.findall(r'(?:id|class|name|for|data-[\w-]+)="([^"]*)"', html))
        assert not re.search(r"\b(?:fail|pass|bad|good|wcag|defect)", attributes, re.I), path.name


def test_compare_shows_metric_changes_calls_and_changed_outcomes():
    from evals.compare import compare

    def run(commit, recall, outcome, calls):
        return {"git_commit": commit, "mode": "agent", "measurement": "lazy", "model": "m", "reviewer_model": None,
                "used_lessons": False, "metrics": {"recall": recall, "precision": 1.0, "decided": 0.5, "false_passes": 0},
                "cases": [{"name": "judgement_bad", "model_calls": calls,
                           "rows": [{"criterion": "1.1.1", "outcome": outcome}, {"criterion": "2.4.2", "outcome": "caught"}]}]}

    lines = "\n".join(compare(run("aaa111", 0.5, "flagged_for_human", 120), run("bbb222", 0.75, "caught", 90)))
    assert "aaa111" in lines and "bbb222" in lines
    assert "recall" in lines and "change +0.250" in lines
    assert "judgement_bad: 120 -> 90" in lines
    assert "judgement_bad 1.1.1: flagged_for_human -> caught" in lines and "2.4.2" not in lines.split("Outcomes")[1]
    a, b = run("aaa111", 0.5, "caught", 1), run("bbb222", 0.75, "caught", 1)
    a["metrics"]["recall_ci"], b["metrics"]["recall_ci"] = [0.2, 0.6], [0.5, 0.9]
    b["measurement"] = "eager"
    lines = "\n".join(compare(a, b))
    assert "(no detectable change)" in lines and "differ in measurement" in lines


def test_the_cost_guard_stops_starting_cases_at_the_cap(monkeypatch, tmp_path):
    import asyncio
    from argparse import Namespace

    import evals.run_eval as run_eval
    seen = []

    async def fake_case(case, args, memory=None, calls_left=10**9):
        seen.append(calls_left)
        return {"name": case["name"], **score(case["expected"], {}), "errors": [], "warnings": [], "model_calls": 600,
                "calls_by_agent": {"Lead auditor": 500, "Reviewer": 100}, "models": None, "reported": True, "lessons_written": 0}
    monkeypatch.setattr(run_eval, "run_case", fake_case)
    monkeypatch.setattr(run_eval, "HERE", tmp_path)
    (tmp_path / "cases.json").write_text(json.dumps({"cases": [{"name": n, "expected": {"1.1.1": "fail"}} for n in "abc"]}))
    args = Namespace(cases_file=str(tmp_path / "cases.json"), cases=None, mode="code", memory="off", repeats=1,
                     max_calls=1000, use_lessons=False, measurement="eager", no_interaction=False, model=None,
                     reviewer_model=None, fail_on_false_pass=False)
    assert asyncio.run(run_eval.main_async(args)) == 5          # the third case never starts
    assert seen == [1000, 400]                                  # each case may spend only what the run has left
    saved = json.loads(next((tmp_path / "results").glob("*.json")).read_text())
    assert (saved["model_calls"], saved["not_started"]) == (1200, 1)
    assert saved["calls_by_agent"] == {"Lead auditor": 1000, "Reviewer": 200}   # where the run's calls went
    args.cases, args.max_calls = ["a"], 600                    # the cap cuts the only case: still exit 5
    assert asyncio.run(run_eval.main_async(args)) == 5


def test_a_miss_keeps_the_findings_that_explain_it():
    """A row that isn't caught or a correct pass carries its criterion's findings (status, source, reviewer verdict),
    so a miss can be explained from the results file without a paid rerun. Baseline mode: no model."""
    import asyncio
    from argparse import Namespace

    import evals.run_eval as run_eval
    case = {"name": "x", "file": "cases/judgement_bad.html", "level": "AA", "expected": {"2.4.4": "fail", "2.4.6": "fail"}}
    args = Namespace(mode="baseline", measurement="eager", no_interaction=False, trace=False, use_lessons=False)
    rows = {r["criterion"]: r for r in asyncio.run(run_eval.run_case(case, args))["rows"]}
    assert rows["2.4.4"]["outcome"] == "caught" and "findings" not in rows["2.4.4"]
    missed = rows["2.4.6"]
    assert missed["outcome"] == "flagged_for_human" and missed["findings"]
    assert set(missed["findings"][0]) == {"status", "original_status", "source", "verdict", "note", "description"}
