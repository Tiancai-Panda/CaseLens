import pytest

from caselens.evaluation import evaluate


def test_metrics_are_hand_calculated_and_missing_data_stays_unknown():
    rows = [
        {"case_id": "1", "truth": "a", "prediction": "a", "status": "completed", "latency_ms": 10, "probabilities": {"a": .8, "b": .2}},
        {"case_id": "2", "truth": "b", "prediction": "a", "status": "completed", "latency_ms": 20, "probabilities": {"a": .6, "b": .4}},
        {"case_id": "3", "truth": "b", "prediction": "b", "status": "needs_review", "latency_ms": 30},
    ]
    metrics = evaluate(rows, labels=["a", "b"])
    assert metrics["accuracy"] == pytest.approx(2 / 3)
    assert metrics["macro_f1"] == pytest.approx(2 / 3)
    assert metrics["auto_coverage"] == pytest.approx(2 / 3)
    assert metrics["auto_error_rate"] == .5
    assert metrics["brier_score"] == pytest.approx(.4)
    assert metrics["brier_cases"] == 2
    assert metrics["p50_latency_ms"] == 20
    assert metrics["p95_latency_ms"] == 29
    assert metrics["cost_total"] is None


def test_invalid_or_duplicate_case_rows_are_rejected():
    row = {"case_id": "1", "truth": "a", "prediction": "a", "status": "completed"}
    with pytest.raises(ValueError, match="duplicate"):
        evaluate([row, row], labels=["a"])
    with pytest.raises(ValueError):
        evaluate([{**row, "probabilities": {"a": .4}}], labels=["a"])


@pytest.mark.parametrize("row", [None, [], 7, {"case_id": "1", "truth": "a", "prediction": "a", "status": "completed", "probabilities": ["a"]}])
def test_nonobject_rows_and_probabilities_are_rejected(row):
    with pytest.raises(ValueError):
        evaluate([row], labels=["a"])
