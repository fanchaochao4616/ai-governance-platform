"""CSV / Excel import helpers with column normalization."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

TEXT_ALIASES = {"text", "content", "文本", "内容", "正文"}
LABEL_ALIASES = {"label", "gold_label", "标签", "标注", "类别"}
ID_ALIASES = {"id", "external_id", "业务id", "业务ID", "ext_id"}


@dataclass
class ParsedRow:
    text: str
    label: str | None = None
    external_id: str | None = None
    source_row: int = 0


@dataclass
class ParseResult:
    rows: list[ParsedRow] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    skipped: int = 0


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    mapping: dict[str, str] = {}
    for col in df.columns:
        key = str(col).strip()
        low = key.lower()
        if low in TEXT_ALIASES or key in TEXT_ALIASES:
            mapping[col] = "text"
        elif low in LABEL_ALIASES or key in LABEL_ALIASES:
            mapping[col] = "label"
        elif low in {a.lower() for a in ID_ALIASES} or key in ID_ALIASES:
            mapping[col] = "external_id"
    return df.rename(columns=mapping)


def read_tabular(path: Path | str, sheet_name: str | int | None = 0) -> pd.DataFrame:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        for encoding in ("utf-8-sig", "utf-8", "gbk"):
            try:
                return pd.read_csv(path, encoding=encoding)
            except UnicodeDecodeError:
                continue
        return pd.read_csv(path)
    if suffix in {".xlsx", ".xlsm", ".xls"}:
        return pd.read_excel(path, sheet_name=sheet_name if sheet_name is not None else 0)
    raise ValueError(f"Unsupported file type: {suffix}. Use .csv or .xlsx")


def parse_dataset(
    path: Path | str,
    *,
    require_label: bool = False,
    strict: bool = False,
) -> ParseResult:
    """Parse CSV/Excel into normalized rows. require_label for gold set."""
    df = read_tabular(path)
    if df.empty:
        return ParseResult(errors=[{"row": 0, "reason": "empty file"}])

    df = _normalize_columns(df)
    if "text" not in df.columns:
        return ParseResult(
            errors=[{"row": 0, "reason": "missing text column (text/content/文本)"}]
        )
    if require_label and "label" not in df.columns:
        return ParseResult(
            errors=[{"row": 0, "reason": "missing label column (label/标签)"}]
        )

    result = ParseResult()
    for i, row in df.iterrows():
        source_row = int(i) + 2  # header = 1
        try:
            text_val = row.get("text")
            if pd.isna(text_val) or str(text_val).strip() == "":
                result.errors.append({"row": source_row, "reason": "empty text"})
                result.skipped += 1
                if strict:
                    break
                continue
            label_val = None
            if "label" in df.columns and not pd.isna(row.get("label")):
                label_val = str(row.get("label")).strip()
            if require_label and not label_val:
                result.errors.append({"row": source_row, "reason": "empty label"})
                result.skipped += 1
                if strict:
                    break
                continue
            ext = None
            if "external_id" in df.columns and not pd.isna(row.get("external_id")):
                ext = str(row.get("external_id")).strip() or None
            result.rows.append(
                ParsedRow(
                    text=str(text_val).strip(),
                    label=label_val,
                    external_id=ext,
                    source_row=source_row,
                )
            )
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"row": source_row, "reason": str(exc)})
            result.skipped += 1
            if strict:
                break
    return result
