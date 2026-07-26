"""Data cleaning APIs: upload dataset, run methods, export.

托管数据集清洗复用数据检索（keywords / regex / vector_fast / vector），
并支持本地大模型（Annotator/Ollama）清洗。
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import data_clean_service
from config import UPLOAD_DIR, ensure_data_dirs

router = APIRouter(prefix="/api/v1/data-clean", tags=["data-clean"])


class CleanMethodStep(BaseModel):
    id: str
    params: dict = Field(default_factory=dict)


class CleanFilterSpec(BaseModel):
    """复用数据检索条件，筛选待清洗子集。query 为空表示不筛选。"""

    mode: str = Field(
        default="keywords",
        description="keywords | regex | vector_fast | vector",
    )
    query: str = Field(default="", description="检索条件")
    keywords: list[str] | None = None
    match: str = Field(default="any", description="any|all")
    case_sensitive: bool = False
    top_k: int = Field(default=50, ge=1, le=200)
    min_score: float | None = None
    limit: int = Field(default=500, ge=1, le=500)


class CleanRunRequest(BaseModel):
    session_id: str
    methods: list[CleanMethodStep] = Field(default_factory=list)
    filter: CleanFilterSpec | None = None


class CleanFromJobRequest(BaseModel):
    job_id: int


class CleanFromDatasetRequest(BaseModel):
    dataset_id: int


@router.get("/methods")
def get_methods() -> dict:
    return {
        "methods": data_clean_service.list_methods(),
        "llm": data_clean_service.local_llm_info(),
    }


@router.get("/llm-info")
def get_llm_info() -> dict:
    return data_clean_service.local_llm_info()


@router.post("/upload")
async def upload_dataset(file: UploadFile = File(...)) -> dict:
    ensure_data_dirs()
    name = file.filename or "upload.bin"
    suffix = Path(name).suffix.lower()
    if suffix not in {".csv", ".xlsx", ".xlsm", ".xls"}:
        raise HTTPException(400, "仅支持 .csv 或 .xlsx")
    dest = UPLOAD_DIR / f"clean_{uuid.uuid4().hex}{suffix}"
    try:
        with dest.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        return data_clean_service.create_session_from_upload(dest, name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(400, f"解析失败: {e}") from e


@router.post("/from-job")
def from_job(body: CleanFromJobRequest, db: Session = Depends(get_db)) -> dict:
    try:
        return data_clean_service.create_session_from_job(db, int(body.job_id))
    except ValueError as e:
        msg = str(e)
        code = 404 if "not found" in msg else 400
        raise HTTPException(code, msg) from e


@router.post("/from-dataset")
def from_dataset(
    body: CleanFromDatasetRequest, db: Session = Depends(get_db)
) -> dict:
    """从数据集管理中的托管数据集载入清洗会话。"""
    try:
        return data_clean_service.create_session_from_dataset(
            db, int(body.dataset_id)
        )
    except ValueError as e:
        msg = str(e)
        code = 404 if "not found" in msg else 400
        raise HTTPException(code, msg) from e


@router.post("/run")
def run_clean(body: CleanRunRequest, db: Session = Depends(get_db)) -> dict:
    try:
        steps = [s.model_dump() for s in body.methods]
        filter_spec: dict[str, Any] | None = None
        if body.filter is not None:
            filter_spec = body.filter.model_dump()
        return data_clean_service.run_clean(
            body.session_id,
            steps,
            filter_spec=filter_spec,
            db=db,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/export")
def export_cleaned(
    session_id: str = Query(...),
    format: str = Query(default="csv"),
) -> FileResponse:
    try:
        path, media, filename = data_clean_service.export_cleaned(
            session_id, fmt=format
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return FileResponse(path, media_type=media, filename=filename)


@router.get("/session/{session_id}")
def get_session(session_id: str) -> dict:
    try:
        sess = data_clean_service.get_session(session_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    df = sess.cleaned_df if sess.cleaned_df is not None else sess.df
    from app.services.data_clean_service import _preview_df, _stats

    return {
        "session_id": sess.session_id,
        "source_name": sess.source_name,
        "dataset_id": sess.dataset_id,
        "text_col": sess.text_col,
        "stats": _stats(df, sess.text_col),
        "has_cleaned": sess.cleaned_df is not None,
        "preview": _preview_df(df),
        "last_report": sess.last_report or None,
        "llm": data_clean_service.local_llm_info(),
    }
