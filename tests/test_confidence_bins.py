from app.services.confidence import (
    assign_bin,
    multi_round_average,
    recommend_bins,
    stratified_sample,
    validate_bins,
)


def test_validate_bins_ok():
    bins = [
        {"name": "Low", "min": 0.0, "max": 0.5},
        {"name": "Medium", "min": 0.5, "max": 0.85},
        {"name": "High", "min": 0.85, "max": 1.0},
    ]
    msgs = validate_bins(bins)
    assert not any("invalid" in m for m in msgs)


def test_validate_bins_overlap():
    bins = [
        {"name": "A", "min": 0.0, "max": 0.6},
        {"name": "B", "min": 0.5, "max": 1.0},
    ]
    msgs = validate_bins(bins)
    assert any("overlap" in m for m in msgs)


def test_assign_and_sample():
    bins = [
        {"name": "Low", "min": 0.0, "max": 0.5},
        {"name": "High", "min": 0.5, "max": 1.0},
    ]
    assert assign_bin(0.2, bins) == "Low"
    assert assign_bin(0.9, bins) == "High"
    items = [
        {"seq": i, "confidence": 0.1 if i < 5 else 0.9, "text": f"t{i}", "pred_label": "a"}
        for i in range(10)
    ]
    sampled = stratified_sample(items, bins, per_bin=2)
    assert len(sampled) == 4
    assert {s["bin_name"] for s in sampled} == {"Low", "High"}


def test_multi_round_average():
    rounds = [
        {"round": 1, "label": "A", "confidence": 0.9},
        {"round": 2, "label": "B", "confidence": 0.8},
        {"round": 3, "label": "A", "confidence": 0.7},
    ]
    label, conflict = multi_round_average(rounds, [1, 2, 3])
    assert label == "A"
    assert conflict is True

    label2, conflict2 = multi_round_average(rounds, [1, 3])
    assert label2 == "A"
    assert conflict2 is False


def test_recommend_bins():
    bins = recommend_bins([0.1, 0.2, 0.5, 0.8, 0.9])
    assert len(bins) == 3
    assert bins[0]["name"] == "Low"
