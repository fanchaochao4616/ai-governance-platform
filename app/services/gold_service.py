"""Gold test set import and dynamic growth."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.models import GoldTestItem, Job
from app.schemas import UploadResult
from app.services.io_tabular import parse_dataset
from app.services.labeling import normalize_gold_label


def import_gold(
    db: Session,
    job: Job,
    file_path: Path,
    *,
    strict: bool = False,
    replace_initial: bool = True,
) -> UploadResult:
    parsed = parse_dataset(file_path, require_label=True, strict=strict)
    if not parsed.rows and parsed.errors:
        raise ValueError(f"Failed to parse gold set: {parsed.errors[0]}")

    if replace_initial:
        db.query(GoldTestItem).filter(
            GoldTestItem.job_id == job.id,
            GoldTestItem.source == "initial",
        ).delete()
        db.flush()

    schema = job.label_schema or {}
    items = [
        GoldTestItem(
            job_id=job.id,
            text=row.text,
            gold_label=normalize_gold_label(str(row.label), schema),
            source="initial",
            external_id=row.external_id,
        )
        for row in parsed.rows
    ]
    db.bulk_save_objects(items)
    job.progress = {
        **(job.progress or {}),
        "gold_initial_count": len(items),
        "gold_file": str(file_path.name),
    }
    db.commit()
    return UploadResult(
        count=len(items),
        errors=parsed.errors,
        skipped=parsed.skipped,
    )


def add_qc_correction(
    db: Session,
    job_id: int,
    *,
    text: str,
    gold_label: str,
    round_id: int | None = None,
    seq: int | None = None,
) -> GoldTestItem:
    item = GoldTestItem(
        job_id=job_id,
        text=text,
        gold_label=gold_label,
        source="qc_correction",
        round_id=round_id,
        seq=seq,
    )
    db.add(item)
    return item

