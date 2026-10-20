"""Small-dataset metrics with explicit abstention and missing-data semantics."""

import math
from collections import Counter


def percentile(values, percent):
    values = sorted(values)
    if not values:
        return None
    index = (len(values)-1)*percent
    lower, upper = math.floor(index), math.ceil(index)
    return values[lower] + (values[upper]-values[lower])*(index-lower)


def evaluate(rows: list[dict], *, labels: list[str]):
    if not rows or not labels or len(set(labels)) != len(labels):
        raise ValueError("provide nonempty rows and distinct labels")
    ids = set()
    pairs, automatic, briers, latencies, costs, model_calls = [], [], [], [], [], []
    statuses = Counter()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each row must be an object")
        if not row.get("case_id") or row["case_id"] in ids:
            raise ValueError("missing or duplicate case_id")
        ids.add(row["case_id"])
        truth, prediction = row.get("truth"), row.get("prediction")
        if truth not in labels or (prediction is not None and prediction not in labels):
            raise ValueError("truth/prediction must use declared labels")
        status = row.get("status")
        if status not in {"completed", "needs_review", "partial", "failed"}:
            raise ValueError("invalid case status")
        if status == "completed" and prediction is None:
            raise ValueError("completed cases require a prediction")
        pairs.append((truth, prediction))
        statuses[status] += 1
        if status == "completed":
            automatic.append((truth, prediction))
        probabilities = row.get("probabilities")
        if probabilities is not None:
            if not isinstance(probabilities, dict):
                raise ValueError("probabilities must be an object")
            if set(probabilities) != set(labels) or any(not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in probabilities.values()) or not math.isclose(sum(probabilities.values()), 1, abs_tol=.005):
                raise ValueError("invalid probability distribution")
            briers.append(sum((probabilities[label] - int(truth == label))**2 for label in labels))
        for field, values in [("latency_ms", latencies), ("cost", costs), ("model_calls", model_calls)]:
            value = row.get(field)
            if value is not None:
                if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise ValueError(f"invalid {field}")
                values.append(value)
    f1s = []
    for label in labels:
        tp = sum(t == label and p == label for t, p in pairs)
        fp = sum(t != label and p == label for t, p in pairs)
        fn = sum(t == label and p != label for t, p in pairs)
        f1s.append(2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0)
    return {"cases": len(rows), "accuracy": sum(t == p for t, p in pairs)/len(rows),
            "macro_f1": sum(f1s)/len(labels), "auto_coverage": len(automatic)/len(rows),
            "auto_error_rate": sum(t != p for t, p in automatic)/len(automatic) if automatic else None,
            "review_rate": statuses["needs_review"]/len(rows), "status_counts": dict(statuses),
            "p50_latency_ms": percentile(latencies, .5), "p95_latency_ms": percentile(latencies, .95),
            "latency_cases": len(latencies), "brier_score": sum(briers)/len(briers) if briers else None,
            "brier_cases": len(briers), "brier_definition": "sum over labels, then mean over cases",
            "cost_total": sum(costs) if len(costs) == len(rows) else None,
            "cost_cases": len(costs), "mean_model_calls": sum(model_calls)/len(model_calls) if model_calls else None,
            "model_call_cases": len(model_calls)}
