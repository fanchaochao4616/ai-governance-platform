"""Evaluation metrics on gold set predictions."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def accuracy(y_true: list[str], y_pred: list[str]) -> float:
    if not y_true:
        return 0.0
    correct = sum(1 for a, b in zip(y_true, y_pred) if a == b)
    return correct / len(y_true)


def macro_f1(y_true: list[str], y_pred: list[str]) -> float:
    labels = sorted(set(y_true) | set(y_pred))
    if not labels:
        return 0.0
    f1s: list[float] = []
    for lb in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == lb and p == lb)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != lb and p == lb)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == lb and p != lb)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        f1s.append(f1)
    return sum(f1s) / len(f1s)


def evaluate_predictions(
    pairs: list[tuple[str, str, str]],
) -> dict[str, Any]:
    """
    pairs: list of (text, gold_label, pred_label)
    """
    y_true = [p[1] for p in pairs]
    y_pred = [p[2] for p in pairs]
    badcases: list[dict[str, str]] = []
    by_label: dict[str, dict[str, int]] = defaultdict(lambda: {"correct": 0, "total": 0})
    for text, gold, pred in pairs:
        by_label[gold]["total"] += 1
        if gold == pred:
            by_label[gold]["correct"] += 1
        else:
            badcases.append({"text": text[:200], "gold": gold, "pred": pred})
    return {
        "accuracy": round(accuracy(y_true, y_pred), 6),
        "macro_f1": round(macro_f1(y_true, y_pred), 6),
        "n": len(pairs),
        "badcase_count": len(badcases),
        "badcases": badcases[:50],
        "per_label": dict(by_label),
    }
