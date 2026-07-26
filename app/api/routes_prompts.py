"""Prompt version routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import PromptVersionCreateBody, PromptVersionOut
from app.services import job_service, prompt_service
from app.services.job_service import active_prompt

router = APIRouter(prefix="/api/v1", tags=["prompts"])


@router.get("/jobs/{job_id}/prompt-versions", response_model=list[PromptVersionOut])
def list_prompt_versions(
    job_id: int, db: Session = Depends(get_db)
) -> list[PromptVersionOut]:
    return [
        PromptVersionOut.model_validate(v)
        for v in prompt_service.list_versions(db, job_id)
    ]


@router.post("/jobs/{job_id}/prompt-versions", response_model=PromptVersionOut)
def create_prompt_version(
    job_id: int,
    body: PromptVersionCreateBody,
    db: Session = Depends(get_db),
) -> PromptVersionOut:
    """人工保存当前编辑的 Prompt 为新版本。"""
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    text = (body.prompt_text or "").strip()
    if not text:
        raise HTTPException(400, "prompt_text required")
    parent = active_prompt(db, job_id)
    if parent and (parent.prompt_text or "").strip() == text:
        raise HTTPException(400, "与当前激活版本无差异，无需保存")
    pv = prompt_service.create_version(
        db,
        job,
        text,
        change_reason=body.change_reason or "人工修改提示词",
        parent_version=parent.version if parent else None,
        activate=True,
        source="human_edit",
    )
    db.commit()
    db.refresh(pv)
    return PromptVersionOut.model_validate(pv)


@router.get("/jobs/{job_id}/prompt-versions/{version}/diff")
def prompt_diff(
    job_id: int, version: int, db: Session = Depends(get_db)
) -> dict:
    try:
        return prompt_service.diff_versions(db, job_id, version)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.post(
    "/jobs/{job_id}/prompt-versions/{version}/rollback",
    response_model=PromptVersionOut,
)
def prompt_rollback(
    job_id: int, version: int, db: Session = Depends(get_db)
) -> PromptVersionOut:
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    try:
        pv = prompt_service.rollback(db, job_id, version)
        db.commit()
        db.refresh(pv)
        return PromptVersionOut.model_validate(pv)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


