"""Prompt template knowledge base routes (create / save / version control)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import (
    TemplateCreate,
    TemplateMetaUpdate,
    TemplateOut,
    TemplateVersionCreate,
    TemplateVersionOut,
)
from app.services import template_service

router = APIRouter(prefix="/api/v1", tags=["templates"])


def _out(db: Session, t) -> TemplateOut:
    return TemplateOut(**template_service.template_to_dict(t, db))


@router.get("/templates", response_model=list[TemplateOut])
def list_templates(
    q: str | None = None,
    category: str | None = None,
    db: Session = Depends(get_db),
) -> list[TemplateOut]:
    return [
        _out(db, t)
        for t in template_service.list_templates(db, q=q, category=category)
    ]


@router.get("/templates/recommend", response_model=list[TemplateOut])
def recommend(
    limit: int = Query(default=5, ge=1, le=50),
    db: Session = Depends(get_db),
) -> list[TemplateOut]:
    return [
        _out(db, t)
        for t in template_service.recommend_templates(db, limit=limit)
    ]


@router.post("/templates", response_model=TemplateOut)
def create_template(
    body: TemplateCreate, db: Session = Depends(get_db)
) -> TemplateOut:
    try:
        t = template_service.create_template(db, body)
        return _out(db, t)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.patch("/templates/{template_id}", response_model=TemplateOut)
def update_template_meta(
    template_id: int,
    body: TemplateMetaUpdate,
    db: Session = Depends(get_db),
) -> TemplateOut:
    try:
        t = template_service.update_meta(db, template_id, body)
        return _out(db, t)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.post(
    "/templates/{template_id}/versions",
    response_model=TemplateVersionOut,
)
def save_template_version(
    template_id: int,
    body: TemplateVersionCreate,
    db: Session = Depends(get_db),
) -> TemplateVersionOut:
    """保存为新版本（版本控制：只增不改）。"""
    try:
        ver = template_service.save_new_version(db, template_id, body)
        return TemplateVersionOut.model_validate(ver)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get(
    "/templates/{template_id}/versions",
    response_model=list[TemplateVersionOut],
)
def list_template_versions(
    template_id: int, db: Session = Depends(get_db)
) -> list[TemplateVersionOut]:
    t = template_service.get_template(db, template_id)
    if not t:
        raise HTTPException(404, "template not found")
    return [
        TemplateVersionOut.model_validate(v)
        for v in template_service.list_versions(db, template_id)
    ]


@router.get("/templates/{template_id}/versions/{version}/diff")
def template_version_diff(
    template_id: int, version: int, db: Session = Depends(get_db)
) -> dict:
    try:
        return template_service.diff_version(db, template_id, version)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.post(
    "/templates/{template_id}/versions/{version}/activate",
    response_model=TemplateVersionOut,
)
def activate_template_version(
    template_id: int, version: int, db: Session = Depends(get_db)
) -> TemplateVersionOut:
    """激活历史版本（回滚/选用）。"""
    try:
        ver = template_service.activate_version(db, template_id, version)
        return TemplateVersionOut.model_validate(ver)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/templates/from-prompt/{version_id}", response_model=TemplateOut)
def from_prompt(
    version_id: int,
    name: str | None = None,
    category: str = "general",
    score: float = 0.0,
    template_id: int | None = None,
    db: Session = Depends(get_db),
) -> TemplateOut:
    """从 Job PromptVersion 新建模板，或 template_id 指定时追加为新版本。"""
    try:
        t = template_service.from_prompt_version(
            db,
            version_id,
            name=name,
            category=category,
            score=score,
            template_id=template_id,
        )
        return _out(db, t)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/templates/{template_id}", response_model=TemplateOut)
def get_template(template_id: int, db: Session = Depends(get_db)) -> TemplateOut:
    t = template_service.get_template(db, template_id)
    if not t:
        raise HTTPException(404, "template not found")
    return _out(db, t)
