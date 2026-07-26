from pathlib import Path

import pandas as pd

from app.services.io_tabular import parse_dataset


def test_parse_csv(tmp_path: Path):
    p = tmp_path / "d.csv"
    p.write_text("text,id\nhello,1\nworld,2\n", encoding="utf-8")
    r = parse_dataset(p)
    assert len(r.rows) == 2
    assert r.rows[0].text == "hello"
    assert r.rows[0].external_id == "1"


def test_parse_excel_gold(tmp_path: Path):
    p = tmp_path / "g.xlsx"
    df = pd.DataFrame({"文本": ["a", "b"], "标签": ["正常", "违规"]})
    df.to_excel(p, index=False)
    r = parse_dataset(p, require_label=True)
    assert len(r.rows) == 2
    assert r.rows[0].label == "正常"


def test_parse_missing_text(tmp_path: Path):
    p = tmp_path / "bad.csv"
    p.write_text("foo,bar\n1,2\n", encoding="utf-8")
    r = parse_dataset(p)
    assert r.rows == []
    assert r.errors
