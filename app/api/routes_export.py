"""Export routes (CSV / Excel)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import export_service, job_service

router = APIRouter(prefix="/api/v1", tags=["export"])


@router.get("/jobs/{job_id}/export")
def export_job(
    job_id: int,
    format: str = Query(default="xlsx", alias="format"),
    db: Session = Depends(get_db),
) -> Response:
    """全量导出：annotations + rounds + gold_test + meta（需已全量标注）。"""
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    try:
        data, media, filename, info = export_service.export_bytes(db, job, fmt=format)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Has-Gold": "1" if info.get("has_gold") else "0",
        "Access-Control-Expose-Headers": "Content-Disposition, X-Has-Gold",
    }
    return Response(content=data, media_type=media, headers=headers)


@router.get("/jobs/{job_id}/export-gold")
def export_gold(
    job_id: int,
    format: str = Query(default="xlsx", alias="format"),
    db: Session = Depends(get_db),
) -> Response:
    """仅导出 Gold Test；无数据时返回 400「没有 Gold Test 内容」。"""
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    try:
        data, media, filename = export_service.export_gold_bytes(db, job, fmt=format)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return Response(
        content=data,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Has-Gold": "1",
            "Access-Control-Expose-Headers": "Content-Disposition, X-Has-Gold",
        },
    )


# 兼容旧路径（曾误做成 export-qc）
@router.get("/jobs/{job_id}/export-qc")
def export_qc_compat(
    job_id: int,
    format: str = Query(default="xlsx", alias="format"),
    db: Session = Depends(get_db),
) -> Response:
    """兼容旧链接：改为导出 Gold Test。"""
    return export_gold(job_id=job_id, format=format, db=db)
