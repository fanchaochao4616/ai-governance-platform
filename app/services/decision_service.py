"""Human per-round decision: continue / stop + gold re-loop + re-label."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Job, QCFeedback, Round
from app.schemas import DecisionRequest
from app.services.annotation_service import start_full_or_subset_round
from app.services.budget import BudgetExceeded
from app.services.events import log_event
from app.services.gold_optimize import (
    begin_new_gold_loop,
    is_gold_loop_active,
    refine_prompt_with_gold,
    _job_lock,
)
from app.services.job_service import active_prompt, set_status
from app.services.prompt_service import create_version
from app.services.qc_service import get_round
from app.state_machine import JobStatus, RoundStatus


# 前端可点「重新标注」的状态（不含已在跑的 GOLD_OPTIMIZING，防双开）
_REANNOTATE_ALLOWED = {
    JobStatus.AWAIT_DECISION.value,
    JobStatus.GOLD_FAILED.value,
    JobStatus.GOLD_READY.value,
    JobStatus.AWAIT_DECISION_THRESHOLD.value,
    JobStatus.AWAIT_CONFIDENCE_BINS.value,
    JobStatus.AWAIT_QC.value,
    JobStatus.PROMPT_IMPROVING.value,
    JobStatus.ABORTED.value,  # 中止后可重新标注恢复
    JobStatus.COMPLETED.value,  # 兼容：误标已完成时仍可开下一轮 loop
    JobStatus.FAILED.value,
}

# 后台入队后状态已是 GOLD_OPTIMIZING，执行时放行
_REANNOTATE_ALLOWED_RUNTIME = _REANNOTATE_ALLOWED | {
    JobStatus.GOLD_OPTIMIZING.value,
}


def apply_decision(
    db: Session,
    job: Job,
    round_no: int,
    body: DecisionRequest,
) -> Job:
    """
    重新标注 / 人工介入下一轮 Gold loop：

    - 允许状态：Gold 失败、QC 决策后，以及阈值/分层等待中（可强制开新 loop）
    - 人工可改 Prompt → 落历史（可选，不强制）
    - GOLD_FAILED 时：不要求 QC、不要求改提示词，点「重新标注」即可开下一轮
    - 仅在此处 begin_new_gold_loop：迭代→0、全量进度→0、当前轮次+1；上限固定
    - 仅达标后才全量/子集重标
    """
    # round_no 优先用调用方传入；否则回退 current_round_no
    if not round_no:
        round_no = int(job.current_round_no or 0)
    rnd = get_round(db, job.id, round_no) if round_no else None
    # 若指定轮次无 Round 记录（纯 Gold loop 失败、尚无全量），回退最近一轮
    if round_no and rnd is None:
        rnd = (
            db.query(Round)
            .filter(Round.job_id == job.id)
            .order_by(Round.round_no.desc())
            .first()
        )

    if job.status not in _REANNOTATE_ALLOWED_RUNTIME:
        raise ValueError(
            f"job not ready for re-annotate (status={job.status})；"
            "需在 Gold 失败 / QC 决策后 / 阈值与分层等待中操作"
        )
    # 已在真正跑 loop（非本请求刚入队）时拒绝，防止双开写乱迭代
    stage = str((job.progress or {}).get("stage") or "")
    if is_gold_loop_active(job) and stage not in {
        "queued_reopen_gold_loop",
        "human_reopen_gold_loop",
    }:
        raise ValueError("Gold 优化进行中，请等待完成后再点「重新标注」")

    if rnd:
        rnd.human_decision = {
            "continue_next": body.continue_next,
            "feedback": body.feedback,
            "next_confidence_ranges": body.next_confidence_ranges,
        }
        if body.continue_next:
            rnd.status = RoundStatus.COMPLETED.value
        db.commit()

    if not body.continue_next:
        set_status(job, JobStatus.AWAIT_DECISION, stage="ready_to_finalize")
        job.progress = {
            **(job.progress or {}),
            "stage": "ready_to_finalize",
            "stopped_at_round": round_no,
        }
        log_event(
            db,
            "decision.stop",
            job_id=job.id,
            payload={"round_no": round_no, "feedback": body.feedback},
        )
        db.commit()
        return job

    # 子集重标：有分层范围且历史上至少完成过一轮标注
    has_prior_label = bool(
        db.query(Round)
        .filter(Round.job_id == job.id)
        .count()
    )
    need_subset = bool(body.next_confidence_ranges) and has_prior_label

    # 进程内串行：同一 job 同时只跑一个 Gold loop
    lock = _job_lock(job.id)
    if not lock.acquire(blocking=False):
        raise ValueError("Gold 优化进行中，请等待完成后再点「重新标注」")

    try:
        return _apply_decision_locked(db, job, round_no, body, rnd, need_subset)
    finally:
        lock.release()


def _apply_decision_locked(
    db: Session,
    job: Job,
    round_no: int,
    body: DecisionRequest,
    rnd: Round | None,
    need_subset: bool,
) -> Job:
    # 进入新 loop 前统一切到优化中
    set_status(
        job,
        JobStatus.GOLD_OPTIMIZING,
        stage="human_reopen_gold_loop",
        gold_iteration=0,
        allow_gold_decrease=True,
    )
    job.error_message = None
    db.commit()

    pv = active_prompt(db, job.id)
    if not pv:
        raise ValueError("no active prompt")

    last_fb = None
    if rnd:
        last_fb = (
            db.query(QCFeedback)
            .filter(QCFeedback.round_id == rnd.id)
            .order_by(QCFeedback.id.desc())
            .first()
        )
    feedback = body.feedback or (last_fb.feedback_text if last_fb else "")
    human_note = (body.change_reason or feedback or "").strip()

    try:
        manual_prompt = (body.prompt_text or "").strip()
        active_text = (pv.prompt_text or "").strip()

        # 1) 人工改了提示词 → 先落历史（不计入 Gold 改进次数）
        if manual_prompt and manual_prompt != active_text:
            create_version(
                db,
                job,
                manual_prompt,
                change_reason=body.change_reason
                or feedback
                or "人工修改提示词",
                parent_version=pv.version,
                qc_feedback_id=last_fb.id if last_fb else None,
                activate=True,
                source="human_edit",
            )
            log_event(
                db,
                "prompt_manual_edit",
                job_id=job.id,
                payload={"round_no": round_no, "from_version": pv.version},
            )
            db.commit()

        # 2) 新 loop：迭代 0、全量进度 0、当前轮次 +1（唯一清零点）
        max_imp = begin_new_gold_loop(job, clear_gold_log=False)
        set_status(
            job,
            JobStatus.GOLD_OPTIMIZING,
            stage="human_reopen_gold_loop",
            gold_iteration=0,
            max_gold_iterations=max_imp,
            loop_round=int(job.current_round_no or 0),
            gold_loop_active=True,
            allow_gold_decrease=True,
        )
        log_event(
            db,
            "decision.reopen_gold_loop",
            job_id=job.id,
            payload={
                "loop_round": int(job.current_round_no or 0),
                "max_imp": max_imp,
                "from_round_no": round_no,
            },
        )
        db.commit()

        _pv, gold_passed = refine_prompt_with_gold(
            db,
            job,
            feedback=human_note
            or "人工介入后重新优化：请结合 Gold 评测改进 Prompt",
            max_improve_rounds=max_imp,
            qc_feedback_id=last_fb.id if last_fb else None,
            always_improve_once=False,
            reset_iteration=False,
        )

        from app.services.abort_service import (
            can_start_full_label,
            gold_meets_target,
            is_abort_requested,
        )

        if is_abort_requested(job) or job.status == JobStatus.ABORTED.value:
            return job

        acc = float((job.last_gold_metrics or {}).get("accuracy") or 0.0)
        target = float(job.target_accuracy or 0.0)
        final_iter = int(job.gold_iteration or 0)
        if final_iter > max_imp:
            final_iter = max_imp
        # 硬校验：不能只信 gold_passed 标志，必须 accuracy >= target
        meets, acc2, target2 = gold_meets_target(job)
        acc = acc2
        target = target2
        if gold_passed and not meets:
            gold_passed = False

        if not gold_passed or not meets:
            # 失败：钉死最终迭代，状态 GOLD_FAILED，禁止全量
            set_status(
                job,
                JobStatus.GOLD_FAILED,
                stage="gold_failed_await_human",
                accuracy=acc,
                gold_iteration=final_iter,
                phase="gold",
                gold_loop_active=False,
            )
            job.error_message = (
                f"Gold 仍未达目标 accuracy={acc:.4f} < {target}；"
                f"已用改进 {final_iter}/{max_imp}。点「重新标注」开启下一轮"
                f"（可不改提示词 / 无需 QC）"
            )
            log_event(
                db,
                "decision.gold_failed_no_relabel",
                job_id=job.id,
                payload={
                    "accuracy": acc,
                    "target": target,
                    "iteration": final_iter,
                    "loop_round": int(job.current_round_no or 0),
                },
            )
            db.commit()
            return job

        set_status(
            job,
            JobStatus.GOLD_READY,
            stage="gold_ready",
            accuracy=acc,
            gold_iteration=final_iter,
            gold_loop_active=False,
        )
        job.error_message = None
        db.commit()

        # 3) 仅达标才标注（再次硬闸门）
        ok, gate_reason = can_start_full_label(job)
        if not ok:
            set_status(
                job,
                JobStatus.GOLD_FAILED,
                stage="gold_failed_gate_blocked",
                gold_loop_active=False,
            )
            job.error_message = f"已阻止全量标注：{gate_reason}"
            db.commit()
            return job

        bins_dict = job.confidence_bins or (rnd.confidence_ranges if rnd else None) or {}
        bins_list = [
            {"name": k, "min": v["min"], "max": v["max"]}
            for k, v in bins_dict.items()
        ]

        if need_subset and bins_list:
            start_full_or_subset_round(
                db,
                job,
                target_ranges=body.next_confidence_ranges,
                bins=bins_list,
            )
        else:
            start_full_or_subset_round(db, job)
    except BudgetExceeded:
        job.status = JobStatus.BUDGET_EXCEEDED.value
        prog = dict(job.progress or {})
        prog["gold_loop_active"] = False
        job.progress = prog
        db.commit()
    except Exception as exc:  # noqa: BLE001
        job.status = JobStatus.GOLD_FAILED.value
        job.error_message = f"重新标注/Gold loop 异常：{exc}"
        prog = dict(job.progress or {})
        prog["phase"] = "gold"
        prog["stage"] = "gold_loop_error"
        prog["gold_loop_active"] = False
        job.progress = prog
        db.commit()
        raise

    return job
