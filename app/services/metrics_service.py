"""Dashboard metrics aggregation."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import AnnotationRecord, EventLog, Job, PromptTemplate, Round
from app.services.budget import remaining
from app.services.job_service import active_prompt
from app.services.labeling import is_threshold_set


def live_annotation_progress(db: Session, job: Job) -> dict[str, Any]:
    """过程可视化：Gold 每轮 Prompt 改动 + Accuracy + 全量标注进度."""
    prog = job.progress or {}
    gold_log = list(prog.get("gold_log") or [])
    total = (
        db.query(func.count(AnnotationRecord.id))
        .filter(AnnotationRecord.job_id == job.id)
        .scalar()
        or 0
    )
    scored = (
        db.query(func.count(AnnotationRecord.id))
        .filter(
            AnnotationRecord.job_id == job.id,
            AnnotationRecord.current_confidence.isnot(None),
        )
        .scalar()
        or 0
    )
    phase = prog.get("phase")
    if not phase:
        if job.status in {
            "GOLD_OPTIMIZING",
            "GOLD_FAILED",
            "GOLD_READY",
            "PROMPT_IMPROVING",
        }:
            phase = "gold"
        elif job.status == "ROUND_LABELING":
            phase = "full_label"
        elif job.status == "AWAIT_DECISION_THRESHOLD":
            phase = "await_threshold"
        elif job.status == "ABORTED":
            phase = "aborted"
        else:
            phase = job.status

    # prefer in-flight progress counters；Gold / 中止阶段不回退到历史 scored（否则会显示 100%）
    in_gold_phase = phase == "gold" or job.status in {
        "GOLD_OPTIMIZING",
        "GOLD_FAILED",
        "GOLD_READY",
        "PROMPT_IMPROVING",
    }
    aborted = (
        job.status == "ABORTED"
        or phase == "aborted"
        or bool(prog.get("full_label_frozen"))
        or bool(prog.get("abort_requested"))
    )
    if aborted:
        # 中止：只信 progress 里冻结的进度，绝不 fallback 到 scored/total
        labeled = int(prog.get("labeled") or 0)
        target = int(prog.get("label_target") or 0)
        percent = float(prog.get("label_percent") or 0)
        if target > 0 and labeled >= 0 and percent <= 0 and labeled > 0:
            percent = round(100.0 * labeled / target, 2)
        # 未开始全量时保持 0
        if target <= 0 and labeled <= 0:
            percent = 0.0
    elif in_gold_phase and prog.get("labeled") is not None:
        labeled = int(prog.get("labeled") or 0)
        target = int(prog.get("label_target") or 0)
        percent = float(prog.get("label_percent") or 0)
    elif job.status == "ROUND_LABELING" or phase == "full_label":
        labeled = int(prog.get("labeled") or 0)
        target = int(prog.get("label_target") or total or 0)
        percent = float(prog.get("label_percent") or 0)
        if target and labeled and not percent:
            percent = round(100.0 * labeled / target, 2)
    else:
        # 阈值/QC 等人阶段：可用 progress；无则不假装 100%
        labeled = int(prog.get("labeled") or 0)
        target = int(prog.get("label_target") or 0)
        percent = float(prog.get("label_percent") or 0)
        if not labeled and not target:
            # 有历史 scored 也不展示为全量进度完成，避免误导
            labeled = 0
            target = 0
            percent = 0.0
        elif target and labeled and not percent:
            percent = round(100.0 * labeled / target, 2)

    pv = active_prompt(db, job.id)
    # 迭代次数：只信 job.gold_iteration 列（失败停在 max/max，点重新标注才清 0）
    # 禁止用 gold_log 反推，避免 log 里 iteration=0 的 start 条目把展示打成 0
    if job.gold_iteration is not None:
        gold_iter = int(job.gold_iteration)
    else:
        gold_iter = int(prog.get("gold_iteration") or 0)
    # 上限固定取 job 配置
    gold_max = max(1, int(job.max_gold_iterations or 3))
    round_no = int(job.current_round_no or 0)

    return {
        "job_id": job.id,
        "status": job.status,
        "phase": phase,
        "pipeline": prog.get("pipeline") or "annotation",
        "error_message": job.error_message,
        "gold": {
            "iteration": gold_iter,
            "max_iterations": gold_max,
            "target_accuracy": job.target_accuracy,
            "last_metrics": job.last_gold_metrics,
            "active_prompt_version": pv.version if pv else None,
            "log": gold_log,
            "latest": prog.get("gold_latest"),
            "loop_round": prog.get("loop_round") or round_no,
        },
        "full_label": {
            "labeled": labeled,
            # 中止且未开始全量时 total 也显示 0，避免 0/100 → 误以为进度条满
            "total": target if target > 0 else (0 if aborted else total),
            "percent": percent,
            "message": prog.get("full_label_message"),
            "round_no": round_no,
            "frozen": bool(prog.get("full_label_frozen") or aborted),
        },
        "current_round_no": round_no,
        "threshold_set": is_threshold_set(job.label_schema),
        "progress_raw": prog,
    }


def job_metrics(db: Session, job: Job) -> dict[str, Any]:
    rounds = (
        db.query(Round)
        .filter(Round.job_id == job.id)
        .order_by(Round.round_no.asc())
        .all()
    )
    events = (
        db.query(EventLog.event, func.count(EventLog.id))
        .filter(EventLog.job_id == job.id)
        .group_by(EventLog.event)
        .all()
    )
    return {
        "job_id": job.id,
        "status": job.status,
        "tokens_used": job.tokens_used,
        "token_budget": job.token_budget,
        "tokens_remaining": remaining(job),
        "gold_iteration": job.gold_iteration,
        "last_gold_metrics": job.last_gold_metrics,
        "current_round_no": job.current_round_no,
        "rounds": [
            {
                "round_no": r.round_no,
                "status": r.status,
                "labeled_count": r.labeled_count,
                "metrics": r.metrics,
                "target_ranges": r.target_ranges_for_labeling,
            }
            for r in rounds
        ],
        "events": {e: c for e, c in events},
        "progress": job.progress,
    }


def global_metrics(db: Session) -> dict[str, Any]:
    job_count = db.query(func.count(Job.id)).scalar() or 0
    completed = (
        db.query(func.count(Job.id)).filter(Job.status == "COMPLETED").scalar() or 0
    )
    tmpl_count = db.query(func.count(PromptTemplate.id)).scalar() or 0
    used = (
        db.query(func.count(PromptTemplate.id))
        .filter(PromptTemplate.usage_count > 0)
        .scalar()
        or 0
    )
    avg_rounds = db.query(func.avg(Job.current_round_no)).scalar() or 0
    return {
        "jobs_total": int(job_count),
        "jobs_completed": int(completed),
        "templates_total": int(tmpl_count),
        "templates_used": int(used),
        "template_reuse_rate": (used / tmpl_count) if tmpl_count else 0.0,
        "avg_rounds": float(avg_rounds),
    }
