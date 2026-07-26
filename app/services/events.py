"""Event / audit logging."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import EventLog


def log_event(
    db: Session,
    event: str,
    *,
    job_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    db.add(EventLog(job_id=job_id, event=event, payload=payload or {}))
