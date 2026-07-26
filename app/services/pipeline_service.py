"""One-click 数据标注 pipeline: Gold Prompt 优化 →（仅达标时）全量标注."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Job
from app.services.annotation_service import start_full_or_subset_round
from app.services.events import log_event
from app.services.gold_optimize import run_gold_optimization
from app.services.job_service import set_status
from app.state_machine import JobStatus


def run_annotation_pipeline(db: Session, job_id: int) -> Job:
    """
    确认开始数据标注后执行：
    1) 初始 Prompt（不计迭代）+ Gold loop（迭代从 0，改进最多 max_gold_iterations 次）
    2) **仅当 Gold 达标 (GOLD_READY)** 才全量标注
    3) 未达标 (GOLD_FAILED) → 停止，等待人工点「重新标注」开下一轮 loop
       （可不改 Prompt / 无需 QC）
    """
    job = db.get(Job, job_id)
    if not job:
        raise ValueError("job not found")

    # 不在此处清零 gold_iteration；由 run_gold_optimization → begin_new_gold_loop 统一处理
    set_status(
        job,
        JobStatus.GOLD_OPTIMIZING,
        stage="pipeline_started",
        pipeline="annotation",
        phase="gold",
    )
    db.commit()

    log_event(
        db,
        "annotation_pipeline.started",
        job_id=job_id,
        payload={},
    )
    db.commit()

    job = run_gold_optimization(db, job_id)
    db.refresh(job)

    from app.services.abort_service import can_start_full_label, is_abort_requested

    # 中止：绝不进全量
    if is_abort_requested(job) or job.status == JobStatus.ABORTED.value:
        log_event(
            db,
            "annotation_pipeline.aborted_no_full_label",
            job_id=job_id,
            payload={"status": job.status},
        )
        db.commit()
    else:
        ok, reason = can_start_full_label(job)
        if ok:
            prog = dict(job.progress or {})
            prog["phase"] = "full_label"
            prog["pipeline"] = "annotation"
            prog["gold_finished_status"] = job.status
            job.progress = prog
            db.commit()

            log_event(
                db,
                "annotation_pipeline.full_label_start",
                job_id=job_id,
                payload={
                    "after_gold_status": job.status,
                    "accuracy": (job.last_gold_metrics or {}).get("accuracy"),
                },
            )
            db.commit()

            start_full_or_subset_round(db, job)
            db.refresh(job)
        else:
            # 未达标 / 状态不对：禁止全量；若误为 GOLD_READY 则纠正为 GOLD_FAILED
            if job.status == JobStatus.GOLD_READY.value:
                acc = float((job.last_gold_metrics or {}).get("accuracy") or 0.0)
                set_status(
                    job,
                    JobStatus.GOLD_FAILED,
                    stage="gold_failed_gate_blocked",
                    gold_loop_active=False,
                )
                job.error_message = (
                    f"Gold 未达目标，已阻止全量标注：{reason}"
                )
                db.commit()
            log_event(
                db,
                "annotation_pipeline.gold_failed_no_full_label",
                job_id=job_id,
                payload={
                    "accuracy": (job.last_gold_metrics or {}).get("accuracy"),
                    "status": job.status,
                    "message": reason,
                },
            )
            db.commit()

    log_event(
        db,
        "annotation_pipeline.finished_phase",
        job_id=job_id,
        payload={"status": job.status},
    )
    db.commit()
    return job
