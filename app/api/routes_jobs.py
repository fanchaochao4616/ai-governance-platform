"""Job, dataset, gold, start, metrics routes."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.schemas import (
    DecisionThresholdRequest,
    JobBulkDeleteRequest,
    JobCreate,
    JobGoldParamsUpdate,
    JobNameUpdate,
    JobOut,
    UploadResult,
)
from app.services import dataset_service, gold_service, job_service
from app.services.annotation_service import (
    apply_decision_threshold,
    apply_multi_round_average,
    confidence_distribution,
    start_full_or_subset_round,
)
from app.services.gold_optimize import run_gold_optimization
from app.services.metrics_service import (
    global_metrics,
    job_metrics,
    live_annotation_progress,
)
from app.services.pipeline_service import run_annotation_pipeline
from app.services.abort_service import abort_job
from app.schemas import FinalizeRequest
from app.state_machine import JobStatus
from config import UPLOAD_DIR, ensure_data_dirs

router = APIRouter(prefix="/api/v1", tags=["jobs"])


def _save_upload(file: UploadFile) -> Path:
    ensure_data_dirs()
    suffix = Path(file.filename or "upload.bin").suffix.lower()
    if suffix not in {".csv", ".xlsx", ".xlsm", ".xls"}:
        raise HTTPException(400, "Only .csv or .xlsx files are supported")
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return dest


@router.post("/jobs", response_model=JobOut)
def create_job(body: JobCreate, db: Session = Depends(get_db)) -> JobOut:
    try:
        job = job_service.create_job(db, body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return job_service.job_to_out(db, job)


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(db: Session = Depends(get_db)) -> list[JobOut]:
    return [job_service.job_to_out(db, j) for j in job_service.list_jobs(db)]


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db)) -> JobOut:
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job_service.job_to_out(db, job)


@router.patch("/jobs/{job_id}/name", response_model=JobOut)
def update_job_name(
    job_id: int,
    body: JobNameUpdate,
    db: Session = Depends(get_db),
) -> JobOut:
    """更新任务显示名称（提示词调试保存版本时可改名）。"""
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    try:
        job = job_service.update_job_name(db, job, body.name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return job_service.job_to_out(db, job)


@router.patch("/jobs/{job_id}/gold-params", response_model=JobOut)
def update_gold_params(
    job_id: int,
    body: JobGoldParamsUpdate,
    db: Session = Depends(get_db),
) -> JobOut:
    """
    更新 Gold 优化参数（目标准确率、最大迭代次数）。
    仅首次开始标注前 / 可重新标注时允许；Gold loop 进行中禁止。
    """
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if body.target_accuracy is None and body.max_gold_iterations is None:
        raise HTTPException(400, "请至少提供 target_accuracy 或 max_gold_iterations")
    try:
        job = job_service.update_gold_params(
            db,
            job,
            target_accuracy=body.target_accuracy,
            max_gold_iterations=body.max_gold_iterations,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return job_service.job_to_out(db, job)


@router.delete("/jobs/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db)) -> dict:
    """永久删除单个 Job 及其全部关联数据。"""
    ok = job_service.delete_job(db, job_id)
    if not ok:
        raise HTTPException(404, "job not found")
    return {"ok": True, "deleted": [job_id], "count": 1}


@router.post("/jobs/bulk-delete")
def bulk_delete_jobs(
    body: JobBulkDeleteRequest,
    db: Session = Depends(get_db),
) -> dict:
    """永久批量删除 Job。"""
    ids = [int(x) for x in (body.ids or []) if x is not None]
    if not ids:
        raise HTTPException(400, "请选择要删除的 Job")
    result = job_service.delete_jobs(db, ids)
    if result["count"] == 0:
        raise HTTPException(404, "未找到可删除的 Job")
    return {"ok": True, **result}


@router.post("/jobs/{job_id}/dataset", response_model=UploadResult)
async def upload_dataset(
    job_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> UploadResult:
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    path = _save_upload(file)
    try:
        return dataset_service.import_dataset(db, job, path)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/jobs/{job_id}/gold", response_model=UploadResult)
async def upload_gold(
    job_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> UploadResult:
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    path = _save_upload(file)
    try:
        return gold_service.import_gold(db, job, path)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


def _bg_gold(job_id: int) -> None:
    db = SessionLocal()
    try:
        run_gold_optimization(db, job_id)
    finally:
        db.close()


def _bg_pipeline(job_id: int) -> None:
    db = SessionLocal()
    try:
        run_annotation_pipeline(db, job_id)
    finally:
        db.close()


@router.post("/jobs/{job_id}/start-annotation")
def start_annotation(
    job_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """数据标注一键确认：Gold Prompt 优化 → 自动全量标注（过程可轮询 live-progress）。"""
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")

    from sqlalchemy import func as sa_func

    from app.models import AnnotationRecord, GoldTestItem

    n_data = (
        db.query(sa_func.count(AnnotationRecord.id))
        .filter(AnnotationRecord.job_id == job_id)
        .scalar()
        or 0
    )
    n_gold = (
        db.query(sa_func.count(GoldTestItem.id))
        .filter(GoldTestItem.job_id == job_id)
        .scalar()
        or 0
    )
    if n_data == 0:
        raise HTTPException(400, "请先上传全量未标注数据")
    if n_gold == 0:
        raise HTTPException(400, "请先上传初始 Gold Test Set")
    if job.status in {
        JobStatus.GOLD_OPTIMIZING.value,
        JobStatus.ROUND_LABELING.value,
        JobStatus.PROMPT_IMPROVING.value,
    }:
        raise HTTPException(400, f"任务进行中（{job.status}），请等待完成")
    if job.status not in {
        JobStatus.CREATED.value,
        JobStatus.GOLD_FAILED.value,
        JobStatus.GOLD_READY.value,
        JobStatus.FAILED.value,
        JobStatus.ABORTED.value,
    }:
        # allow re-run only from early/failed/aborted states
        if job.current_round_no and job.current_round_no > 0:
            raise HTTPException(
                400,
                f"已开始多轮标注（status={job.status}），请走后续阈值/QC/决策流程",
            )

    background_tasks.add_task(_bg_pipeline, job_id)
    job_service.set_status(
        job,
        JobStatus.GOLD_OPTIMIZING,
        stage="queued_annotation_pipeline",
        phase="gold",
        pipeline="annotation",
    )
    db.commit()
    return {
        "ok": True,
        "status": job.status,
        "message": "数据标注已启动：Gold Prompt 优化 → 全量标注",
    }


@router.post("/jobs/{job_id}/start")
def start_gold(
    job_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """兼容旧接口：仅 Gold 优化（不含自动全量）。推荐使用 start-annotation。"""
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job.status not in {
        JobStatus.CREATED.value,
        JobStatus.GOLD_FAILED.value,
        JobStatus.GOLD_READY.value,
    }:
        if job.status not in {JobStatus.GOLD_FAILED.value, JobStatus.CREATED.value}:
            if job.status != JobStatus.GOLD_READY.value:
                raise HTTPException(400, f"cannot start from status {job.status}")
    background_tasks.add_task(_bg_gold, job_id)
    job_service.set_status(job, JobStatus.GOLD_OPTIMIZING, stage="queued")
    db.commit()
    return {"ok": True, "status": job.status}


def _bg_full_label(job_id: int) -> None:
    from app.models import Job

    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job:
            start_full_or_subset_round(db, job)
    finally:
        db.close()


@router.post("/jobs/{job_id}/start-full-label")
def start_full_label(
    job_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """兼容旧接口：仅全量标注。推荐使用 start-annotation 一键流水线。"""
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    from app.services.abort_service import can_start_full_label

    ok, reason = can_start_full_label(job)
    if not ok:
        raise HTTPException(400, reason)
    if job.status not in {
        JobStatus.GOLD_READY.value,
        JobStatus.GOLD_FAILED.value,
        JobStatus.AWAIT_CONFIDENCE_BINS.value,
    }:
        if job.status not in {
            JobStatus.GOLD_READY.value,
            JobStatus.GOLD_FAILED.value,
        }:
            raise HTTPException(400, f"cannot start full label from {job.status}")
    background_tasks.add_task(_bg_full_label, job_id)
    job_service.set_status(job, JobStatus.ROUND_LABELING, stage="queued_full_label")
    db.commit()
    return {"ok": True, "status": job.status}


@router.get("/jobs/{job_id}/live-progress")
def live_progress(job_id: int, db: Session = Depends(get_db)) -> dict:
    """数据标注过程可视化：Gold 迭代日志 + Prompt diff + 全量进度."""
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return live_annotation_progress(db, job)


@router.post("/jobs/{job_id}/abort")
def abort_running_job(job_id: int, db: Session = Depends(get_db)) -> dict:
    """
    中止当前进度（状态 ABORTED，可恢复）。
    不是终止（COMPLETED / CANCELLED）；之后可点「重新标注」继续。
    """
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    try:
        return abort_job(db, job)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/jobs/{job_id}/confidence-distribution")
def conf_dist(job_id: int, db: Session = Depends(get_db)) -> dict:
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return confidence_distribution(db, job_id)


@router.get("/jobs/{job_id}/gold-eval")
def gold_eval_detail(job_id: int, db: Session = Depends(get_db)) -> dict:
    """
    返回最近一次 Gold 评测明细（文本、金标、小模型预测标签与置信度）。
    数据来自 job.last_gold_metrics.details（评测时写入）。
    """
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    m = job.last_gold_metrics or {}
    details = list(m.get("details") or [])
    # 若旧数据无 details，仅返回汇总
    from app.services.job_service import active_prompt

    pv = active_prompt(db, job_id)
    return {
        "job_id": job.id,
        "accuracy": m.get("accuracy"),
        "macro_f1": m.get("macro_f1"),
        "n": m.get("n") or len(details),
        "badcase_count": m.get("badcase_count"),
        "target_accuracy": job.target_accuracy,
        "gold_eval_threshold": m.get("gold_eval_threshold"),
        "prompt_version": pv.version if pv else None,
        "details": details,
    }


@router.post("/jobs/{job_id}/decision-threshold")
def set_decision_threshold(
    job_id: int,
    body: DecisionThresholdRequest,
    db: Session = Depends(get_db),
) -> dict:
    """全量标注完成后设置判定阈值，批量推导 label 1/0。"""
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    # 有标注轮次即可设/重设阈值（可多次应用重算 0/1）
    if not (job.current_round_no and job.current_round_no > 0):
        raise HTTPException(400, "请先完成至少一轮标注再设置判定阈值")
    # Gold 失败/优化中禁止改阈值，否则会把 GOLD_FAILED 覆盖成 AWAIT_CONFIDENCE_BINS，
    # 导致无法点「重新标注」进入下一轮 loop
    if job.status in {
        JobStatus.ROUND_LABELING.value,
        JobStatus.GOLD_OPTIMIZING.value,
        JobStatus.PROMPT_IMPROVING.value,
        JobStatus.CREATED.value,
        JobStatus.GOLD_FAILED.value,
        JobStatus.GOLD_READY.value,
        JobStatus.FAILED.value,
        JobStatus.BUDGET_EXCEEDED.value,
        JobStatus.ABORTED.value,
    }:
        raise HTTPException(
            400,
            f"当前状态不能设置判定阈值"
            f"（已中止/Gold 失败请点「重新标注」；标注进行中请等待）",
        )
    try:
        return apply_decision_threshold(
            db, job, body.threshold, round_no=body.round_no
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/jobs/{job_id}/finalize")
def finalize(
    job_id: int,
    body: FinalizeRequest,
    db: Session = Depends(get_db),
) -> dict:
    """
    多轮平均。默认不结束任务（保持可重新标注 / QC）。
    若 body 带 mark_completed=true 则标记 COMPLETED。
    """
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    mark_completed = bool(getattr(body, "mark_completed", False))
    n = apply_multi_round_average(
        db,
        job,
        from_round=body.from_round,
        to_round=body.to_round,
        selected_rounds=body.selected_rounds,
        mark_completed=mark_completed,
    )
    return {"ok": True, "updated": n, "status": job.status}


@router.get("/jobs/{job_id}/metrics")
def metrics(job_id: int, db: Session = Depends(get_db)) -> dict:
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job_metrics(db, job)


@router.get("/metrics")
def metrics_global(db: Session = Depends(get_db)) -> dict:
    return global_metrics(db)


