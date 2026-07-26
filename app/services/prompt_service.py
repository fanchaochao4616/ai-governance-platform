"""Prompt versioning: create, list, diff, rollback."""

from __future__ import annotations

import difflib
from typing import Any

from sqlalchemy.orm import Session

from app.models import Job, PromptVersion


def list_versions(db: Session, job_id: int) -> list[PromptVersion]:
    return (
        db.query(PromptVersion)
        .filter(PromptVersion.job_id == job_id)
        .order_by(PromptVersion.version.asc())
        .all()
    )


def get_version(db: Session, job_id: int, version: int) -> PromptVersion | None:
    return (
        db.query(PromptVersion)
        .filter(PromptVersion.job_id == job_id, PromptVersion.version == version)
        .first()
    )


def next_version_number(db: Session, job_id: int) -> int:
    last = (
        db.query(PromptVersion)
        .filter(PromptVersion.job_id == job_id)
        .order_by(PromptVersion.version.desc())
        .first()
    )
    return (last.version + 1) if last else 1


def deactivate_all(db: Session, job_id: int) -> None:
    for pv in db.query(PromptVersion).filter(PromptVersion.job_id == job_id).all():
        pv.is_active = False


def create_version(
    db: Session,
    job: Job,
    prompt_text: str,
    *,
    change_reason: str | None = None,
    metrics: dict[str, Any] | None = None,
    parent_version: int | None = None,
    qc_feedback_id: int | None = None,
    tokens_used: int = 0,
    improvement_suggestion: dict[str, Any] | None = None,
    activate: bool = True,
    source: str | None = None,
) -> PromptVersion:
    """创建新 Prompt 版本（只增不改）。Diff 可对 parent_version 计算。"""
    ver = next_version_number(db, job.id)
    if activate:
        deactivate_all(db, job.id)

    reason = (change_reason or "").strip() or "（未填写修改说明）"
    sugg: dict[str, Any] = dict(improvement_suggestion or {})
    if source:
        sugg.setdefault("source", source)

    # 与上一版做 unified diff，写入 suggestion 便于历史展示
    parent = None
    if parent_version is not None:
        parent = get_version(db, job.id, parent_version)
    elif ver > 1:
        parent = get_version(db, job.id, ver - 1)
    if parent is not None:
        old = (parent.prompt_text or "").splitlines(keepends=True)
        new = (prompt_text or "").splitlines(keepends=True)
        diff = "".join(
            difflib.unified_diff(
                old,
                new,
                fromfile=f"v{parent.version}",
                tofile=f"v{ver}",
            )
        )
        if diff:
            sugg["diff"] = diff
        sugg.setdefault("parent_version", parent.version)

    pv = PromptVersion(
        job_id=job.id,
        version=ver,
        prompt_text=prompt_text,
        parent_version=parent.version if parent else parent_version,
        metrics=metrics,
        change_reason=reason,
        qc_feedback_id=qc_feedback_id,
        tokens_used=tokens_used,
        is_active=activate,
        improvement_suggestion=sugg or None,
    )
    db.add(pv)
    db.flush()
    return pv


def diff_versions(
    db: Session, job_id: int, version: int
) -> dict[str, Any]:
    cur = get_version(db, job_id, version)
    if not cur:
        raise ValueError("version not found")
    parent = None
    if cur.parent_version is not None:
        parent = get_version(db, job_id, cur.parent_version)
    elif version > 1:
        parent = get_version(db, job_id, version - 1)

    old = (parent.prompt_text if parent else "").splitlines(keepends=True)
    new = cur.prompt_text.splitlines(keepends=True)
    diff = "".join(
        difflib.unified_diff(
            old,
            new,
            fromfile=f"v{parent.version if parent else 0}",
            tofile=f"v{cur.version}",
        )
    )
    return {
        "version": cur.version,
        "parent_version": parent.version if parent else None,
        "diff": diff,
        "current": cur.prompt_text,
        "parent_text": parent.prompt_text if parent else None,
    }


def rollback(db: Session, job_id: int, version: int) -> PromptVersion:
    target = get_version(db, job_id, version)
    if not target:
        raise ValueError("version not found")
    # Create a new active version that copies the rolled-back text
    job = db.get(Job, job_id)
    if not job:
        raise ValueError("job not found")
    return create_version(
        db,
        job,
        target.prompt_text,
        change_reason=f"rollback to v{version}",
        parent_version=target.version,
        metrics=target.metrics,
        activate=True,
    )



