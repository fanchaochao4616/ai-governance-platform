"""Dataset upload and permanent seq assignment."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.models import AnnotationRecord, Job
from app.schemas import UploadResult
from app.services.io_tabular import parse_dataset
from app.state_machine import JobStatus


def import_dataset(
    db: Session,
    job: Job,
    file_path: Path,
    *,
    strict: bool = False,
) -> UploadResult:
    if job.status not in {
        JobStatus.CREATED.value,
        JobStatus.GOLD_FAILED.value,
        JobStatus.GOLD_READY.value,
    }:
        # allow re-import only before labeling starts
        if job.current_round_no and job.current_round_no > 0:
            raise ValueError("Cannot re-import dataset after labeling rounds started")

    existing = (
        db.query(AnnotationRecord)
        .filter(AnnotationRecord.job_id == job.id)
        .count()
    )
    if existing:
        # replace only in CREATED
        if job.status != JobStatus.CREATED.value:
            raise ValueError("Dataset already imported; create a new job to re-upload")
        db.query(AnnotationRecord).filter(AnnotationRecord.job_id == job.id).delete()
        db.flush()

    parsed = parse_dataset(file_path, require_label=False, strict=strict)
    if not parsed.rows and parsed.errors:
        raise ValueError(f"Failed to parse dataset: {parsed.errors[0]}")

    records: list[AnnotationRecord] = []
    for i, row in enumerate(parsed.rows, start=1):
        records.append(
            AnnotationRecord(
                job_id=job.id,
                seq=i,  # permanent primary key within job
                text=row.text,
                external_id=row.external_id,
                rounds=[],
                final_label=None,
                current_label=None,
                current_confidence=None,
                conflict=False,
            )
        )
    db.bulk_save_objects(records)
    job.progress = {
        **(job.progress or {}),
        "dataset_count": len(records),
        "dataset_file": str(file_path.name),
    }
    db.commit()
    return UploadResult(
        count=len(records),
        errors=parsed.errors,
        skipped=parsed.skipped,
    )
