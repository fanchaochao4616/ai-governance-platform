"""Confidence-threshold labeling helpers (binary labels: 1 / 0).

判定阈值在**全量标注完成之后**由用户设置，再批量把 confidence 映射为 1/0。
Gold 优化阶段仅用内部默认阈值算 Accuracy，不要求用户提前设。
"""

from __future__ import annotations

from typing import Any

from config import (
    DEFAULT_DECISION_THRESHOLD,
    NEGATIVE_LABEL,
    POSITIVE_LABEL,
)


def default_label_schema(
    decision_threshold: float | None = None,
    *,
    threshold_set: bool = False,
) -> dict[str, Any]:
    """Create job schema. threshold_set=False until user applies after full labeling."""
    th: float | None
    if threshold_set and decision_threshold is not None:
        th = max(0.0, min(1.0, float(decision_threshold)))
    else:
        th = None
    return {
        "mode": "confidence_threshold",
        "positive_label": POSITIVE_LABEL,  # "1"
        "negative_label": NEGATIVE_LABEL,  # "0"
        "decision_threshold": th,
        "threshold_set": bool(threshold_set),
        "labels": [
            {"name": POSITIVE_LABEL, "description": "confidence >= decision_threshold"},
            {"name": NEGATIVE_LABEL, "description": "confidence < decision_threshold"},
        ],
    }


def is_threshold_set(label_schema: dict[str, Any] | None) -> bool:
    if not label_schema:
        return False
    if label_schema.get("threshold_set"):
        return True
    th = label_schema.get("decision_threshold")
    return th is not None and label_schema.get("threshold_set") is not False


def get_threshold(
    label_schema: dict[str, Any] | None,
    *,
    fallback: float | None = None,
) -> float | None:
    """Return user-set threshold, or fallback (e.g. for gold eval). None if unset."""
    if label_schema and label_schema.get("decision_threshold") is not None:
        try:
            return max(
                0.0, min(1.0, float(label_schema["decision_threshold"]))
            )
        except (TypeError, ValueError):
            pass
    if fallback is not None:
        return max(0.0, min(1.0, float(fallback)))
    return None


def get_positive_label(label_schema: dict[str, Any] | None = None) -> str:
    if label_schema and label_schema.get("positive_label") is not None:
        return str(label_schema["positive_label"])
    return POSITIVE_LABEL


def get_negative_label(label_schema: dict[str, Any] | None = None) -> str:
    if label_schema and label_schema.get("negative_label") is not None:
        return str(label_schema["negative_label"])
    return NEGATIVE_LABEL


def derive_label(
    confidence: float,
    label_schema: dict[str, Any] | None = None,
    *,
    threshold: float | None = None,
) -> str:
    """confidence >= threshold → 1, else 0."""
    th = threshold
    if th is None:
        th = get_threshold(label_schema)
    if th is None:
        raise ValueError("decision_threshold not set yet")
    conf = max(0.0, min(1.0, float(confidence)))
    if conf >= float(th):
        return get_positive_label(label_schema)
    return get_negative_label(label_schema)


def normalize_gold_label(
    raw: str,
    label_schema: dict[str, Any] | None = None,
) -> str:
    """Map gold values to binary 1 / 0 (canonical labels)."""
    pos = get_positive_label(label_schema)
    neg = get_negative_label(label_schema)
    s = str(raw or "").strip()
    if s in {pos, neg}:
        return s
    # numeric-like
    try:
        if float(s) == 1.0:
            return pos
        if float(s) == 0.0:
            return neg
    except (TypeError, ValueError):
        pass
    low = s.lower()
    positive_aliases = {
        "1",
        "1.0",
        "true",
        "yes",
        "y",
        "是",
        "满足",
        "命中",
        "违规",
        "positive",
        "pos",
        "符合",
    }
    negative_aliases = {
        "0",
        "0.0",
        "false",
        "no",
        "n",
        "否",
        "不满足",
        "未命中",
        "正常",
        "negative",
        "neg",
        "不符合",
    }
    if low in positive_aliases:
        return pos
    if low in negative_aliases:
        return neg
    return s


def set_threshold_on_schema(
    label_schema: dict[str, Any] | None,
    threshold: float,
) -> dict[str, Any]:
    schema = dict(label_schema or default_label_schema())
    schema["decision_threshold"] = max(0.0, min(1.0, float(threshold)))
    schema["threshold_set"] = True
    schema["positive_label"] = schema.get("positive_label") or POSITIVE_LABEL
    schema["negative_label"] = schema.get("negative_label") or NEGATIVE_LABEL
    return schema
