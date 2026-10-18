"""The eval harness's scoring and cases, without running a model."""

import json
from collections import Counter

from evals.run_eval import HERE, metrics, score
from agent_access.catalogue.catalogue import load_catalogue


def test_score_and_metrics():
    expected = {"1.1.1": "fail", "2.4.2": "fail", "3.1.2": "fail", "2.4.6": "pass", "1.4.1": "pass"}
    actual = {"1.1.1": "fail", "2.4.2": "pass", "3.1.2": "needs_review", "2.4.6": "fail", "1.4.1": "pass"}
    result = score(expected, actual)
    assert {r["criterion"]: r["outcome"] for r in result["rows"]} == {
        "1.1.1": "caught", "2.4.2": "false_pass", "3.1.2": "flagged_for_human", "2.4.6": "false_fail",
        "1.4.1": "correct_pass"}
    m = metrics(Counter(result["totals"]))
    assert m == {"recall": round(1 / 3, 3), "precision": 0.5, "decided": 0.8, "false_passes": 1}
    assert score({"1.2.1": "fail"}, {})["rows"][0]["outcome"] == "flagged_for_human"  # missing = not_tested


def test_cases_are_well_formed():
    spec = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    catalogue = load_catalogue()
    for case in spec["cases"]:
        assert (HERE / case["file"]).is_file(), case["file"]
        for criterion, status in case["expected"].items():
            assert catalogue.get(criterion) is not None and status in ("pass", "fail"), (case["name"], criterion)


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
