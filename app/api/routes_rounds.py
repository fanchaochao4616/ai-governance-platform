"""Round QC and decision routes."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.schemas import ConfidenceBinsRequest, DecisionRequest, QCSampleOut, QCSubmitRequest, RoundOut
from app.services import job_service, qc_service
from app.services.decision_service import apply_decision
from app.state_machine import JobStatus

router = APIRouter(prefix="/api/v1", tags=["rounds"])


@router.get("/jobs/{job_id}/rounds")
def list_rounds(job_id: int, db: Session = Depends(get_db)) -> list[RoundOut]:
    from app.models import Round

    rows = (
        db.query(Round)
        .filter(Round.job_id == job_id)
        .order_by(Round.round_no.asc())
        .all()
    )
    return [RoundOut.model_validate(r) for r in rows]


@router.get("/jobs/{job_id}/rounds/{round_no}", response_model=RoundOut)
def get_round(job_id: int, round_no: int, db: Session = Depends(get_db)) -> RoundOut:
    rnd = qc_service.get_round(db, job_id, round_no)
    if not rnd:
        raise HTTPException(404, "round not found")
    return RoundOut.model_validate(rnd)


@router.get("/jobs/{job_id}/recommend-bins")
def recommend_bins(job_id: int, db: Session = Depends(get_db)) -> dict:
    return {"bins": qc_service.recommend_bins_for_job(db, job_id)}


@router.post("/jobs/{job_id}/rounds/{round_no}/confidence-bins")
def define_bins(
    job_id: int,
    round_no: int,
    body: ConfidenceBinsRequest,
    db: Session = Depends(get_db),
) -> dict:
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    try:
        rnd = qc_service.define_bins_and_sample(db, job, round_no, body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {
        "ok": True,
        "round": RoundOut.model_validate(rnd).model_dump(),
        "qc_count": len(qc_service.list_qc_samples(db, job_id, round_no)),
    }


@router.get(
    "/jobs/{job_id}/rounds/{round_no}/qc-samples",
    response_model=list[QCSampleOut],
)
def qc_samples(
    job_id: int,
    round_no: int,
    db: Session = Depends(get_db),
    fallback: bool = True,
) -> list[QCSampleOut]:
    """
    获取 QC 样本。fallback=true（默认）时：当前轮无样本则回退最近有 QC 的轮次
    （中止后仍能看到上一次抽检，不会被清空）。
    """
    if fallback:
        samples, actual_rn = qc_service.list_qc_samples_for_display(
            db, job_id, round_no
        )
        return [
            QCSampleOut.model_validate(qc_service.qc_sample_to_out(s, actual_rn))
            for s in samples
        ]
    samples = qc_service.list_qc_samples(db, job_id, round_no)
    return [
        QCSampleOut.model_validate(qc_service.qc_sample_to_out(s, round_no))
        for s in samples
    ]


@router.post("/jobs/{job_id}/rounds/{round_no}/qc")
def submit_qc(
    job_id: int,
    round_no: int,
    body: QCSubmitRequest,
    db: Session = Depends(get_db),
) -> dict:
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    try:
        return qc_service.submit_qc(db, job, round_no, body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


def _bg_decision(job_id: int, round_no: int, body: DecisionRequest) -> None:
    db = SessionLocal()
    try:
        job = job_service.get_job(db, job_id)
        if not job:
            return
        try:
            apply_decision(db, job, round_no, body)
        except Exception as exc:  # noqa: BLE001
            # 后台异常不能静默：写回错误，状态尽量回到可再次「重新标注」
            db.rollback()
            job = job_service.get_job(db, job_id)
            if job:
                job.error_message = f"重新标注失败：{exc}"
                if job.status not in {
                    JobStatus.GOLD_OPTIMIZING.value,
                    JobStatus.ROUND_LABELING.value,
                }:
                    job.status = JobStatus.GOLD_FAILED.value
                db.commit()
    finally:
        db.close()


@router.post("/jobs/{job_id}/rounds/{round_no}/decision")
def decision(
    job_id: int,
    round_no: int,
    body: DecisionRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """
    重新标注 / 人工开启下一轮 Gold loop。
    Gold 未达标时也可调用（round_no 可为 0）。
    """
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    # 统一用有效轮次；0 时回退 current_round_no
    if not round_no:
        round_no = int(job.current_round_no or 0)
    # 同步校验：避免排队后后台静默失败（状态不允许时立刻 400）
    from app.services.decision_service import _REANNOTATE_ALLOWED

    if body.continue_next and job.status not in _REANNOTATE_ALLOWED:
        # GOLD_OPTIMIZING 且 loop 活跃 → 明确提示勿重复点
        from app.services.gold_optimize import is_gold_loop_active

        if job.status == JobStatus.GOLD_OPTIMIZING.value and is_gold_loop_active(job):
            raise HTTPException(400, "Gold 优化进行中，请等待完成后再点「重新标注」")
        raise HTTPException(
            400,
            f"当前状态「{job.status}」不能重新标注；"
            "请在 Gold未达标/已中止/待QC/待决策/已完成 等可恢复状态下操作",
        )
    if body.continue_next:
        from app.services.gold_optimize import is_gold_loop_active

        if is_gold_loop_active(job):
            raise HTTPException(400, "Gold 优化进行中，请等待完成后再点「重新标注」")
        # 先切状态，让前端立刻看到进入 loop（后台真正跑 Gold）
        job_service.set_status(
            job,
            JobStatus.GOLD_OPTIMIZING,
            stage="queued_reopen_gold_loop",
            phase="gold",
            gold_loop_active=True,
            allow_gold_decrease=True,
        )
        job.error_message = None
        db.commit()
        background_tasks.add_task(_bg_decision, job_id, round_no, body)
        return {
            "ok": True,
            "queued": True,
            "continue_next": True,
            "status": job.status,
            "current_round_no": job.current_round_no,
        }
    try:
        job = apply_decision(db, job, round_no, body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "queued": False, "status": job.status, "continue_next": False}
