from app.services.labeling import (
    default_label_schema,
    derive_label,
    is_threshold_set,
    normalize_gold_label,
    set_threshold_on_schema,
)


def test_threshold_not_set_at_create():
    schema = default_label_schema(threshold_set=False)
    assert is_threshold_set(schema) is False
    assert schema.get("decision_threshold") is None


def test_derive_label_threshold():
    schema = set_threshold_on_schema(default_label_schema(), 0.7)
    assert is_threshold_set(schema)
    assert derive_label(0.7, schema) == "1"
    assert derive_label(0.69, schema) == "0"
    assert derive_label(1.0, schema) == "1"


def test_normalize_gold():
    schema = default_label_schema(threshold_set=False)
    assert normalize_gold_label("1", schema) == "1"
    assert normalize_gold_label("0", schema) == "0"
    assert normalize_gold_label("是", schema) == "1"
    assert normalize_gold_label("否", schema) == "0"
    assert normalize_gold_label(1, schema) == "1"  # type: ignore[arg-type]
    assert normalize_gold_label("满足", schema) == "1"  # legacy alias
