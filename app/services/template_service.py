"""Prompt template knowledge base with version control."""

from __future__ import annotations

import difflib
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import PromptTemplate, PromptTemplateVersion, PromptVersion
from app.schemas import TemplateCreate, TemplateMetaUpdate, TemplateVersionCreate


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_legacy_versions(db: Session) -> None:
    """Migrate old templates that have prompt_text but no version rows."""
    templates = db.query(PromptTemplate).all()
    for t in templates:
        n = (
            db.query(func.count(PromptTemplateVersion.id))
            .filter(PromptTemplateVersion.template_id == t.id)
            .scalar()
            or 0
        )
        if n == 0 and (t.prompt_text or "").strip():
            db.add(
                PromptTemplateVersion(
                    template_id=t.id,
                    version=int(t.current_version or 1),
                    prompt_text=t.prompt_text,
                    change_reason="legacy import",
                    parent_version=None,
                    is_active=True,
                    source_job_id=t.source_job_id,
                    source_prompt_version_id=t.source_prompt_version_id,
                )
            )
            t.current_version = int(t.current_version or 1)
    db.commit()


def _deactivate_versions(db: Session, template_id: int) -> None:
    for v in (
        db.query(PromptTemplateVersion)
        .filter(PromptTemplateVersion.template_id == template_id)
        .all()
    ):
        v.is_active = False


def _next_version(db: Session, template_id: int) -> int:
    last = (
        db.query(PromptTemplateVersion)
        .filter(PromptTemplateVersion.template_id == template_id)
        .order_by(PromptTemplateVersion.version.desc())
        .first()
    )
    return (last.version + 1) if last else 1


def _sync_head(t: PromptTemplate, ver: PromptTemplateVersion) -> None:
    t.prompt_text = ver.prompt_text
    t.current_version = ver.version
    t.updated_at = _utcnow()


def create_template(db: Session, body: TemplateCreate) -> PromptTemplate:
    text = (body.prompt_text or "").strip()
    if not text:
        raise ValueError("prompt_text is required")
    t = PromptTemplate(
        name=body.name.strip(),
        category=body.category or "general",
        description=(body.description or "").strip() or None,
        prompt_text=text,
        current_version=1,
        score=body.score,
        tags=body.tags or [],
        source_job_id=body.source_job_id,
        source_prompt_version_id=body.source_prompt_version_id,
    )
    db.add(t)
    db.flush()
    ver = PromptTemplateVersion(
        template_id=t.id,
        version=1,
        prompt_text=text,
        change_reason=body.change_reason or "initial create",
        parent_version=None,
        is_active=True,
        source_job_id=body.source_job_id,
        source_prompt_version_id=body.source_prompt_version_id,
    )
    db.add(ver)
    db.commit()
    db.refresh(t)
    return t


def update_meta(
    db: Session, template_id: int, body: TemplateMetaUpdate
) -> PromptTemplate:
    t = db.get(PromptTemplate, template_id)
    if not t:
        raise ValueError("template not found")
    if body.name is not None:
        t.name = body.name.strip()
    if body.category is not None:
        t.category = body.category
    if body.description is not None:
        t.description = body.description
    if body.score is not None:
        t.score = body.score
    if body.tags is not None:
        t.tags = body.tags
    t.updated_at = _utcnow()
    db.commit()
    db.refresh(t)
    return t


def save_new_version(
    db: Session,
    template_id: int,
    body: TemplateVersionCreate,
) -> PromptTemplateVersion:
    t = db.get(PromptTemplate, template_id)
    if not t:
        raise ValueError("template not found")
    text = (body.prompt_text or "").strip()
    if not text:
        raise ValueError("prompt_text is required")

    active = active_version(db, template_id)
    parent = active.version if active else None
    if active and active.prompt_text == text:
        raise ValueError("正文与当前激活版本相同，未创建新版本")

    _deactivate_versions(db, template_id)
    ver_no = _next_version(db, template_id)
    ver = PromptTemplateVersion(
        template_id=template_id,
        version=ver_no,
        prompt_text=text,
        change_reason=body.change_reason or f"save v{ver_no}",
        parent_version=parent,
        is_active=True,
        source_job_id=body.source_job_id,
        source_prompt_version_id=body.source_prompt_version_id,
    )
    db.add(ver)
    _sync_head(t, ver)
    db.commit()
    db.refresh(ver)
    return ver


def from_prompt_version(
    db: Session,
    version_id: int,
    *,
    name: str | None = None,
    category: str = "general",
    score: float = 0.0,
    tags: list[str] | None = None,
    template_id: int | None = None,
) -> PromptTemplate:
    """从 Job PromptVersion 创建新模板，或追加为已有模板的新版本。"""
    pv = db.get(PromptVersion, version_id)
    if not pv:
        raise ValueError("prompt version not found")
    metrics = pv.metrics or {}
    auto_score = float(metrics.get("accuracy") or score or 0.0)

    if template_id:
        save_new_version(
            db,
            template_id,
            TemplateVersionCreate(
                prompt_text=pv.prompt_text,
                change_reason=f"from job{pv.job_id} prompt v{pv.version}",
                source_job_id=pv.job_id,
                source_prompt_version_id=pv.id,
            ),
        )
        t = db.get(PromptTemplate, template_id)
        assert t is not None
        if auto_score:
            t.score = max(float(t.score or 0), auto_score)
            db.commit()
            db.refresh(t)
        return t

    return create_template(
        db,
        TemplateCreate(
            name=name or f"job{pv.job_id}-v{pv.version}",
            category=category,
            prompt_text=pv.prompt_text,
            score=auto_score,
            tags=tags or [],
            source_job_id=pv.job_id,
            source_prompt_version_id=pv.id,
            change_reason=f"from job{pv.job_id} prompt v{pv.version}",
        ),
    )


def list_templates(
    db: Session,
    *,
    q: str | None = None,
    category: str | None = None,
) -> list[PromptTemplate]:
    ensure_legacy_versions(db)
    query = db.query(PromptTemplate)
    if category:
        query = query.filter(PromptTemplate.category == category)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                PromptTemplate.name.ilike(like),
                PromptTemplate.prompt_text.ilike(like),
                PromptTemplate.category.ilike(like),
            )
        )
    return query.order_by(PromptTemplate.score.desc(), PromptTemplate.id.desc()).all()


def get_template(db: Session, template_id: int) -> PromptTemplate | None:
    ensure_legacy_versions(db)
    return db.get(PromptTemplate, template_id)


def recommend_templates(db: Session, limit: int = 5) -> list[PromptTemplate]:
    ensure_legacy_versions(db)
    return (
        db.query(PromptTemplate)
        .order_by(PromptTemplate.score.desc(), PromptTemplate.usage_count.desc())
        .limit(limit)
        .all()
    )


def list_versions(db: Session, template_id: int) -> list[PromptTemplateVersion]:
    ensure_legacy_versions(db)
    return (
        db.query(PromptTemplateVersion)
        .filter(PromptTemplateVersion.template_id == template_id)
        .order_by(PromptTemplateVersion.version.asc())
        .all()
    )


def active_version(
    db: Session, template_id: int
) -> PromptTemplateVersion | None:
    return (
        db.query(PromptTemplateVersion)
        .filter(
            PromptTemplateVersion.template_id == template_id,
            PromptTemplateVersion.is_active.is_(True),
        )
        .order_by(PromptTemplateVersion.version.desc())
        .first()
    )


def get_version(
    db: Session, template_id: int, version: int
) -> PromptTemplateVersion | None:
    return (
        db.query(PromptTemplateVersion)
        .filter(
            PromptTemplateVersion.template_id == template_id,
            PromptTemplateVersion.version == version,
        )
        .first()
    )


def activate_version(
    db: Session, template_id: int, version: int
) -> PromptTemplateVersion:
    """回滚/切换到历史版本（设为 active，并同步 head）。"""
    t = db.get(PromptTemplate, template_id)
    if not t:
        raise ValueError("template not found")
    ver = get_version(db, template_id, version)
    if not ver:
        raise ValueError("version not found")
    _deactivate_versions(db, template_id)
    ver.is_active = True
    _sync_head(t, ver)
    db.commit()
    db.refresh(ver)
    return ver


def _unified_diff_text(old_text: str, new_text: str, from_tag: str, to_tag: str) -> str:
    """Stable unified diff; always use newline-terminated logical lines."""
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    # Ensure trailing empty file still diffs cleanly
    lines = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=from_tag,
            tofile=to_tag,
            lineterm="",
            n=3,
        )
    )
    if not lines and (old_text or "") != (new_text or ""):
        # fallback: treat whole text as one block
        lines = list(
            difflib.unified_diff(
                [old_text or ""],
                [new_text or ""],
                fromfile=from_tag,
                tofile=to_tag,
                lineterm="",
            )
        )
    return "\n".join(lines)


def _diff_html_rows(diff_text: str) -> list[dict[str, str]]:
    """Parse unified diff into rows for UI coloring."""
    rows: list[dict[str, str]] = []
    for line in (diff_text or "").splitlines():
        if line.startswith("+++") or line.startswith("---"):
            kind = "file"
        elif line.startswith("@@"):
            kind = "hunk"
        elif line.startswith("+"):
            kind = "add"
        elif line.startswith("-"):
            kind = "del"
        elif line.startswith("\\"):
            kind = "meta"
        else:
            kind = "ctx"
        rows.append({"kind": kind, "text": line})
    return rows


def diff_version(
    db: Session, template_id: int, version: int
) -> dict[str, Any]:
    cur = get_version(db, template_id, version)
    if not cur:
        raise ValueError("version not found")
    parent = None
    if cur.parent_version is not None:
        parent = get_version(db, template_id, cur.parent_version)
    elif version > 1:
        parent = get_version(db, template_id, version - 1)

    from_tag = f"v{parent.version}" if parent else "v0 (empty)"
    to_tag = f"v{cur.version}"
    parent_text = parent.prompt_text if parent else ""
    diff = _unified_diff_text(parent_text, cur.prompt_text or "", from_tag, to_tag)
    if not diff:
        if not parent:
            diff = (
                f"--- {from_tag}\n+++ {to_tag}\n"
                + "\n".join(f"+{ln}" for ln in (cur.prompt_text or "").splitlines())
            )
            if not (cur.prompt_text or "").strip():
                diff = f"(v{cur.version} 为初始版本且正文为空，无对比内容)"
        else:
            diff = f"(v{cur.version} 与 v{parent.version} 正文完全相同)"

    return {
        "template_id": template_id,
        "version": cur.version,
        "parent_version": parent.version if parent else None,
        "from_label": from_tag,
        "to_label": to_tag,
        "diff": diff,
        "diff_rows": _diff_html_rows(diff),
        "current": cur.prompt_text,
        "parent_text": parent_text if parent else None,
        "change_reason": cur.change_reason,
        "has_parent": parent is not None,
    }


def template_to_dict(t: PromptTemplate, db: Session | None = None) -> dict[str, Any]:
    version_count = None
    if db is not None:
        version_count = (
            db.query(func.count(PromptTemplateVersion.id))
            .filter(PromptTemplateVersion.template_id == t.id)
            .scalar()
            or 0
        )
    return {
        "id": t.id,
        "name": t.name,
        "category": t.category,
        "description": t.description,
        "prompt_text": t.prompt_text,
        "current_version": t.current_version or 1,
        "version_count": version_count,
        "score": t.score,
        "tags": t.tags or [],
        "source_job_id": t.source_job_id,
        "source_prompt_version_id": t.source_prompt_version_id,
        "usage_count": t.usage_count or 0,
        "created_at": t.created_at,
        "updated_at": getattr(t, "updated_at", None),
    }
