"""Confidence bin validation, stratification, multi-round average."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def validate_bins(bins: list[dict[str, Any]]) -> list[str]:
    """Return list of warning/error messages. Empty if OK (warnings allowed)."""
    messages: list[str] = []
    if not bins:
        return ["at least one bin required"]
    names = [b.get("name") for b in bins]
    if len(names) != len(set(names)):
        messages.append("duplicate bin names")
    for b in bins:
        lo, hi = float(b["min"]), float(b["max"])
        if lo < 0 or hi > 1 or lo > hi:
            messages.append(f"invalid range for {b.get('name')}: [{lo}, {hi}]")
    # overlap check (sorted by min)
    ordered = sorted(bins, key=lambda x: float(x["min"]))
    for i in range(len(ordered) - 1):
        a, b = ordered[i], ordered[i + 1]
        if float(a["max"]) > float(b["min"]):
            messages.append(
                f"overlap between {a.get('name')} and {b.get('name')}"
            )
    return messages


def assign_bin(confidence: float, bins: list[dict[str, Any]]) -> str | None:
    c = float(confidence)
    for b in bins:
        lo, hi = float(b["min"]), float(b["max"])
        name = str(b["name"])
        # inclusive max for top bin style: use [min, max] for all
        if lo <= c <= hi:
            return name
        # half-open alternative: if max is exclusive except 1.0
        if lo <= c < hi:
            return name
    # fallback nearest
    return None


def recommend_bins(confidences: list[float]) -> list[dict[str, Any]]:
    """Simple tertile-style recommendation."""
    if not confidences:
        return [
            {"name": "Low", "min": 0.0, "max": 0.5},
            {"name": "Medium", "min": 0.5, "max": 0.85},
            {"name": "High", "min": 0.85, "max": 1.0},
        ]
    vals = sorted(float(c) for c in confidences)
    n = len(vals)
    t1 = vals[max(0, n // 3 - 1)]
    t2 = vals[max(0, (2 * n) // 3 - 1)]
    # ensure increasing
    low_max = min(max(t1, 0.01), 0.98)
    med_max = min(max(t2, low_max + 0.01), 0.99)
    return [
        {"name": "Low", "min": 0.0, "max": round(low_max, 4)},
        {"name": "Medium", "min": round(low_max, 4), "max": round(med_max, 4)},
        {"name": "High", "min": round(med_max, 4), "max": 1.0},
    ]


def stratified_sample(
    items: list[dict[str, Any]],
    bins: list[dict[str, Any]],
    per_bin: int,
    *,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """
    items: [{seq, confidence, text, pred_label, ...}]
    returns sampled items with bin_name set.
    """
    import random

    rng = random.Random(seed)
    by_bin: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for it in items:
        conf = it.get("confidence")
        if conf is None:
            continue
        name = assign_bin(float(conf), bins)
        if name is None:
            continue
        row = dict(it)
        row["bin_name"] = name
        by_bin[name].append(row)

    sampled: list[dict[str, Any]] = []
    for b in bins:
        name = str(b["name"])
        pool = by_bin.get(name, [])
        rng.shuffle(pool)
        sampled.extend(pool[: min(per_bin, len(pool))])
    return sampled


def in_named_ranges(
    confidence: float | None,
    bins: list[dict[str, Any]],
    range_names: list[str],
) -> bool:
    if confidence is None:
        return False
    name = assign_bin(float(confidence), bins)
    return name is not None and name in range_names


def multi_round_average(
    round_results: list[dict[str, Any]],
    selected_rounds: list[int] | None = None,
) -> tuple[str | None, bool]:
    """
    Majority vote; ties broken by highest mean confidence.
    Returns (final_label, conflict).
    """
    if not round_results:
        return None, False
    filtered = round_results
    if selected_rounds is not None:
        sel = set(selected_rounds)
        filtered = [r for r in round_results if int(r.get("round", -1)) in sel]
    if not filtered:
        return None, False

    labels = [str(r.get("label")) for r in filtered if r.get("label") is not None]
    if not labels:
        return None, False

    counts = Counter(labels)
    top_count = max(counts.values())
    top_labels = [lb for lb, c in counts.items() if c == top_count]
    conflict = len(set(labels)) > 1

    if len(top_labels) == 1:
        return top_labels[0], conflict

    # tie-break by mean confidence
    best_label = top_labels[0]
    best_conf = -1.0
    for lb in top_labels:
        confs = [
            float(r.get("confidence") or 0.0)
            for r in filtered
            if str(r.get("label")) == lb
        ]
        mean_c = sum(confs) / len(confs) if confs else 0.0
        if mean_c > best_conf:
            best_conf = mean_c
            best_label = lb
    return best_label, True
