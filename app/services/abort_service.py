"""Human abort (中止) of in-flight job work — recoverable, not permanent terminate."""

from __future__ import annotations

import threading
from typing import Any

from sqlalchemy.orm import Session

from app.models import Job
from app.services.events import log_event
from app.services.job_service import set_status
from app.state_machine import JobStatus

# 进程内中止信号：跨 Session 立刻可见（后台 Gold/标注循环轮询）
# 注意：finalize_abort 后仍保留，直到 begin_new_gold_loop / 明确 clear，
# 防止后台循环在 clear 后继续跑并把状态写成 GOLD_READY → 误进全量。
_ABORT_FLAGS: set[int] = set()
_ABORT_GUARD = threading.Lock()

# 可中止的进行中状态
ABORTABLE_STATUSES = {
    JobStatus.GOLD_OPTIMIZING.value,
    JobStatus.ROUND_LABELING.value,
    JobStatus.PROMPT_IMPROVING.value,
}


def request_abort(job: Job) -> None:
    """标记中止请求（进行中任务在循环边界检查后停止）。"""
    with _ABORT_GUARD:
        _ABORT_FLAGS.add(int(job.id))
    prog = dict(job.progress or {})
    prog["abort_requested"] = True
    prog["stage"] = "abort_requested"
    # 作废当前后台 run，防止旧 loop 继续写 GOLD_READY
    prog["run_gen"] = int(prog.get("run_gen") or 0) + 1
    job.progress = prog


def clear_abort_request(job_id: int, job: Job | None = None) -> None:
    """仅在开启新 loop / 新开跑时清除中止信号。"""
    with _ABORT_GUARD:
        _ABORT_FLAGS.discard(int(job_id))
    if job is not None:
        prog = dict(job.progress or {})
        prog["abort_requested"] = False
        job.progress = prog


def is_abort_requested(job: Job) -> bool:
    jid = int(job.id)
    with _ABORT_GUARD:
        if jid in _ABORT_FLAGS:
            return True
    if job.status == JobStatus.ABORTED.value:
        return True
    return bool((job.progress or {}).get("abort_requested"))


def pin_aborted_on_job(job: Job) -> None:
    """
    后台 Session 可能仍持有中止前的 status（如 GOLD_OPTIMIZING）。
    在 commit 前若检测到中止信号，强制钉成 ABORTED，避免把中止状态刷掉。
    """
    if not is_abort_requested(job):
        return
    job.status = JobStatus.ABORTED.value
    prog = dict(job.progress or {})
    prog["abort_requested"] = True
    prog["gold_loop_active"] = False
    prog["phase"] = "aborted"
    if prog.get("stage") not in {"aborted", "abort_requested"}:
        prog["stage"] = "aborted"
    job.progress = prog


def commit_respecting_abort(db: Session, job: Job) -> None:
    """commit 前钉死中止状态，防止后台旧对象覆盖 ABORTED。"""
    pin_aborted_on_job(job)
    db.commit()


def gold_meets_target(job: Job) -> tuple[bool, float, float]:
    """返回 (是否达标, accuracy, target)。"""
    acc = float((job.last_gold_metrics or {}).get("accuracy") or 0.0)
    target = float(job.target_accuracy or 0.0)
    return acc >= target and acc > 0.0, acc, target


def can_start_full_label(job: Job) -> tuple[bool, str]:
    """
    全量标注硬闸门：必须同时满足
    - 状态 GOLD_READY
    - 无中止信号
    - last_gold_metrics.accuracy >= target_accuracy
    """
    if is_abort_requested(job):
        return False, "任务已中止，禁止全量标注"
    if job.status == JobStatus.ABORTED.value:
        return False, "任务处于中止状态，禁止全量标注"
    if job.status != JobStatus.GOLD_READY.value:
        return False, f"Gold 未就绪（status={job.status}），禁止全量标注"
    ok, acc, target = gold_meets_target(job)
    if not ok:
        return (
            False,
            f"Gold 准确率未达标 accuracy={acc:.4f} < target={target:.4f}，禁止全量标注",
        )
    return True, "ok"


def finalize_abort(db: Session, job: Job, *, reason: str = "人工中止") -> Job:
    """
    落地中止状态 ABORTED（可恢复，非 CANCELLED 终止）。
    保留 gold_iteration / 当前轮次 / 已标注结果，仅停止当前自动流程。

    重要：不清除进程内 abort flag，避免后台 loop 在 clear 后继续跑并误进全量。
    flag 仅在 begin_new_gold_loop（重新开跑）时清除。
    """
    # 确保 flag 仍在
    with _ABORT_GUARD:
        _ABORT_FLAGS.add(int(job.id))

    final_iter = int(job.gold_iteration or 0)
    prog = dict(job.progress or {})
    # 冻结全量进度快照：只保留中止瞬间的 labeled/percent，禁止 UI 回退到历史 scored=100%
    was_labeling = (
        job.status == JobStatus.ROUND_LABELING.value
        or prog.get("phase") == "full_label"
    )
    labeled = int(prog.get("labeled") or 0)
    label_target = int(prog.get("label_target") or 0)
    label_percent = float(prog.get("label_percent") or 0)
    if not was_labeling:
        # Gold 优化中中止：全量进度应保持 0（或已有冻结值），不要变成 100%
        if prog.get("phase") in (None, "gold", "aborted") and not prog.get(
            "full_label_frozen"
        ):
            # 若从未在本 loop 跑过全量，清成 0
            if labeled <= 0 or prog.get("phase") == "gold":
                labeled = 0
                label_target = 0
                label_percent = 0.0
    elif label_target > 0 and labeled > 0 and label_percent <= 0:
        label_percent = round(100.0 * labeled / label_target, 2)

    prog["abort_requested"] = True  # 保持，直到新 loop
    prog["gold_loop_active"] = False
    prog["phase"] = "aborted"
    prog["stage"] = "aborted"
    prog["aborted_reason"] = reason
    prog["gold_iteration"] = final_iter
    prog["iteration"] = final_iter
    prog["labeled"] = labeled
    prog["label_target"] = label_target
    prog["label_percent"] = label_percent
    prog["full_label_frozen"] = True
    if was_labeling and labeled > 0:
        prog["full_label_message"] = (
            f"全量标注已中止：{labeled}/{label_target or '—'}（{label_percent:.1f}%）"
        )
    elif not was_labeling:
        prog["full_label_message"] = "全量未开始或已中止（进度保持）"
    # 作废进行中的后台 run
    prog["run_gen"] = int(prog.get("run_gen") or 0) + 1
    job.progress = prog

    set_status(
        job,
        JobStatus.ABORTED,
        stage="aborted",
        phase="aborted",
        gold_loop_active=False,
        abort_requested=True,
        gold_iteration=final_iter,
        allow_gold_decrease=True,
    )
    job.gold_iteration = final_iter
    job.error_message = (
        f"已中止：{reason}。"
        f"当前 Gold 迭代 {final_iter}/{int(job.max_gold_iterations or 3)}，"
        f"轮次 {int(job.current_round_no or 0)}。"
        "可点「重新标注」继续下一轮 loop（非永久终止）。"
    )
    log_event(
        db,
        "job.aborted",
        job_id=job.id,
        payload={
            "reason": reason,
            "gold_iteration": final_iter,
            "current_round_no": job.current_round_no,
            "run_gen": prog.get("run_gen"),
        },
    )
    db.commit()
    db.refresh(job)
    return job


def abort_job(db: Session, job: Job) -> dict[str, Any]:
    """
    用户点击「中止」：
    - 发中止信号并落地 ABORTED
    - 不是 COMPLETED/CANCELLED 终止
    """
    if job.status == JobStatus.ABORTED.value:
        # 确保 flag 仍在，防止旧后台复活
        request_abort(job)
        db.commit()
        return {
            "ok": True,
            "status": job.status,
            "message": "任务已处于中止状态",
            "immediate": True,
        }

    if job.status == JobStatus.COMPLETED.value:
        raise ValueError("任务已完成，无法中止（已终止）")
    if job.status == JobStatus.CANCELLED.value:
        raise ValueError("任务已取消（终止），无法中止")

    request_abort(job)
    db.commit()

    job = finalize_abort(db, job, reason="用户点击中止")
    return {
        "ok": True,
        "status": job.status,
        "message": "已中止当前进度（可重新标注继续，非永久终止）",
        "immediate": True,
        "gold_iteration": job.gold_iteration,
        "current_round_no": job.current_round_no,
    }
