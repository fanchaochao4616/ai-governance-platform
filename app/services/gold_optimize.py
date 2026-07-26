"""Gold test set dual-agent optimization loop (with live progress log)."""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.agents.qc_agent import QCAgent
from app.models import GoldTestItem, Job
from app.services.annotation_service import evaluate_on_gold
from app.services.budget import BudgetExceeded, check_budget
from app.services.events import log_event
from app.services.job_service import active_prompt, set_status
from app.services.prompt_service import create_version
from app.state_machine import JobStatus


def _aborted(job: Job) -> bool:
    from app.services.abort_service import is_abort_requested

    return is_abort_requested(job) or job.status == JobStatus.ABORTED.value


def _commit(db: Session, job: Job) -> None:
    from app.services.abort_service import commit_respecting_abort

    commit_respecting_abort(db, job)

# 进程内按 job_id 串行 Gold loop，防止双重点击/双后台任务把迭代写乱
_JOB_GOLD_LOCKS: dict[int, threading.Lock] = {}
_JOB_GOLD_LOCKS_GUARD = threading.Lock()


def _job_lock(job_id: int) -> threading.Lock:
    with _JOB_GOLD_LOCKS_GUARD:
        lk = _JOB_GOLD_LOCKS.get(job_id)
        if lk is None:
            lk = threading.Lock()
            _JOB_GOLD_LOCKS[job_id] = lk
        return lk


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_max_gold_iterations(job: Job) -> int:
    """Gold 改进次数上限固定取 job 字段，不随 loop / progress 衰减。"""
    return max(1, int(job.max_gold_iterations or 3))


def _clear_full_label_progress(prog: dict[str, Any]) -> dict[str, Any]:
    """清空全量标注进度展示字段（写 0，避免 UI 回退到历史计数）。"""
    prog["labeled"] = 0
    prog["label_target"] = 0
    prog["label_percent"] = 0.0
    prog["full_label_message"] = "全量进度已清空，等待 Gold 达标后重新标注"
    prog["full_label_frozen"] = False
    return prog


def is_gold_loop_active(job: Job) -> bool:
    prog = job.progress or {}
    return bool(prog.get("gold_loop_active")) and job.status in {
        JobStatus.GOLD_OPTIMIZING.value,
        JobStatus.PROMPT_IMPROVING.value,
    }


def _sync_gold_iteration(
    job: Job,
    n: int,
    *,
    loop_id: str | None = None,
    allow_decrease: bool = False,
) -> int:
    """
    同步列字段与 progress。
    - 同一 loop 内默认单调不减（防止 commit/refresh/并发把 3 写回 2/1）
    - allow_decrease=True 仅用于 begin_new_gold_loop 清零
    """
    n = max(0, int(n))
    if not allow_decrease:
        prev = int(job.gold_iteration or 0)
        if n < prev:
            # 拒绝回退；仍写 progress 的 max 与 loop 元数据
            n = prev
    job.gold_iteration = n
    prog = dict(job.progress or {})
    if loop_id is not None:
        # 若已不是本 loop 的 owner，不覆盖别人的进度
        cur_id = prog.get("gold_loop_id")
        if cur_id and cur_id != loop_id:
            return int(job.gold_iteration or 0)
        prog["gold_loop_id"] = loop_id
    prog["gold_iteration"] = n
    prog["iteration"] = n
    prog["max_gold_iterations"] = _job_max_gold_iterations(job)
    job.progress = prog
    return n


def begin_new_gold_loop(job: Job, *, clear_gold_log: bool = False) -> int:
    """
    仅在「首次开跑」或用户点击「重新标注」时调用。

    - gold_iteration → 0
    - 全量进度 → 0
    - current_round_no += 1
    - 分配新 gold_loop_id，标记 gold_loop_active
    """
    max_imp = _job_max_gold_iterations(job)
    loop_id = uuid.uuid4().hex
    job.gold_iteration = 0
    job.error_message = None
    job.current_round_no = int(job.current_round_no or 0) + 1

    # 新 loop 清除中止信号
    from app.services.abort_service import clear_abort_request

    clear_abort_request(job.id, None)

    prog = dict(job.progress or {})
    prog = _clear_full_label_progress(prog)
    if clear_gold_log:
        prog["gold_log"] = []
        prog.pop("gold_latest", None)
    prog["phase"] = "gold"
    prog["pipeline"] = "annotation"
    prog["gold_iteration"] = 0
    prog["iteration"] = 0
    prog["max_gold_iterations"] = max_imp
    prog["loop_round"] = int(job.current_round_no or 0)
    prog["gold_loop_id"] = loop_id
    prog["gold_loop_active"] = True
    prog["abort_requested"] = False
    job.progress = prog
    return max_imp


def _end_gold_loop_flag(job: Job, loop_id: str | None = None) -> None:
    prog = dict(job.progress or {})
    if loop_id is not None and prog.get("gold_loop_id") not in (None, loop_id):
        return
    prog["gold_loop_active"] = False
    job.progress = prog


# 兼容旧名
def _reset_for_new_gold_loop(job: Job, *, clear_gold_log: bool = False) -> int:
    return begin_new_gold_loop(job, clear_gold_log=clear_gold_log)


def _append_gold_log(
    job: Job,
    entry: dict[str, Any],
    *,
    loop_id: str | None = None,
) -> None:
    prog = dict(job.progress or {})
    if loop_id is not None and prog.get("gold_loop_id") not in (None, loop_id):
        # 已被更新的 loop 接管，丢弃本 loop 的日志写入，避免覆盖
        return
    log = list(prog.get("gold_log") or [])
    entry = {**entry, "at": _utcnow_iso()}
    if loop_id is not None:
        entry.setdefault("loop_id", loop_id)
    log.append(entry)
    # 限制日志长度，避免 JSON 过大
    if len(log) > 200:
        log = log[-200:]
    prog["gold_log"] = log
    prog["phase"] = "gold"
    prog["pipeline"] = "annotation"
    prog["gold_latest"] = entry
    prog["max_gold_iterations"] = _job_max_gold_iterations(job)
    # 以列字段为准，且不因 log 回写把迭代改小
    cur = int(job.gold_iteration or 0)
    logged = entry.get("iteration")
    if logged is not None:
        try:
            cur = max(cur, int(logged))
        except (TypeError, ValueError):
            pass
    job.gold_iteration = cur
    prog["gold_iteration"] = cur
    prog["iteration"] = cur
    if loop_id is not None:
        prog["gold_loop_id"] = loop_id
        prog["gold_loop_active"] = True
    job.progress = prog


def evaluate_active_prompt_on_gold(
    db: Session,
    job: Job,
    *,
    log: bool = True,
    iteration: int | None = None,
    loop_id: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    """
    用当前激活 Prompt 在 Gold Test 上重测准确率，结果写入版本 metrics 与 job.last_gold_metrics。
    """
    pv = active_prompt(db, job.id)
    if not pv:
        raise ValueError("no active prompt")
    check_budget(job, 200)
    # 评测前锁住展示用的迭代，避免 on_usage 中间态干扰
    it = iteration if iteration is not None else int(job.gold_iteration or 0)
    _sync_gold_iteration(job, it, loop_id=loop_id, allow_decrease=False)
    metrics = evaluate_on_gold(db, job, pv.prompt_text)
    # 评测过程中 on_usage 可能 commit；评测后再强制写回本步迭代（单调）
    _sync_gold_iteration(job, it, loop_id=loop_id, allow_decrease=False)
    pv.metrics = metrics
    job.last_gold_metrics = metrics
    acc = float(metrics.get("accuracy") or 0.0)
    if log:
        _append_gold_log(
            job,
            {
                "step": "evaluated",
                "iteration": it,
                "version": pv.version,
                "accuracy": acc,
                "macro_f1": metrics.get("macro_f1"),
                "badcase_count": metrics.get("badcase_count"),
                "n": metrics.get("n"),
                "target_accuracy": job.target_accuracy,
                "passed": acc >= float(job.target_accuracy or 0),
                "message": (
                    f"Gold 重测：Prompt v{pv.version} "
                    f"Accuracy={acc:.2%} "
                    f"(目标 {float(job.target_accuracy or 0):.0%}) "
                    f"[{it}/{_job_max_gold_iterations(job)}]"
                ),
            },
            loop_id=loop_id,
        )
    _commit(db, job)
    db.refresh(pv)
    return pv, metrics


def refine_prompt_with_gold(
    db: Session,
    job: Job,
    *,
    feedback: str = "",
    max_improve_rounds: int | None = None,
    qc_feedback_id: int | None = None,
    always_improve_once: bool = False,
    reset_iteration: bool = False,
) -> tuple[Any, bool]:
    """
    Gold 优化 loop（本轮内 **improve_used 仅本地单调递增**）：

    - 控制流只信本地 improve_used，不从 DB refresh 回读计数
    - 落库时单调不减，防止 3→2 / 2→1 等回跳
    - 达到 max 后停止并保持 max/max，等待人工点「重新标注」
    """
    max_imp = _job_max_gold_iterations(job)
    _ = max_improve_rounds

    if reset_iteration:
        begin_new_gold_loop(job, clear_gold_log=False)

    max_imp = _job_max_gold_iterations(job)
    target = float(job.target_accuracy or 0.9)
    loop_id = str((job.progress or {}).get("gold_loop_id") or uuid.uuid4().hex)
    prog0 = dict(job.progress or {})
    prog0["gold_loop_id"] = loop_id
    prog0["gold_loop_active"] = True
    prog0["max_gold_iterations"] = max_imp
    job.progress = prog0

    # 控制流：只使用本地计数。新 loop 从 0 开始；若未 reset 则从当前列读取一次。
    improve_used = int(job.gold_iteration or 0)
    # 保险：上限内夹紧
    if improve_used > max_imp:
        improve_used = max_imp

    qc = QCAgent(
        job.label_schema or {},
        job.policy_rules or "",
        # 重要：不在 on_usage 里 commit，避免会话 expire 导致迭代被旧值覆盖
        on_usage=lambda n: _safe_add_no_commit(job, n),
    )

    def _persist(stage: str, used: int, **extra: Any) -> None:
        """统一落盘：单调写迭代 + 状态 + 立即 commit。中止后不再覆盖 ABORTED。"""
        if _aborted(job):
            return
        used = _sync_gold_iteration(
            job, used, loop_id=loop_id, allow_decrease=False
        )
        set_status(
            job,
            JobStatus.GOLD_OPTIMIZING,
            stage=stage,
            gold_iteration=used,
            max_gold_iterations=max_imp,
            iteration=used,
            loop_round=int(job.current_round_no or 0),
            gold_loop_id=loop_id,
            gold_loop_active=True,
            **extra,
        )
        # set_status 可能再次写 gold_iteration；强制以 used 为准
        _sync_gold_iteration(job, used, loop_id=loop_id, allow_decrease=False)
        _commit(db, job)

    _persist("gold_loop_start", improve_used)
    _append_gold_log(
        job,
        {
            "step": "gold_loop_start",
            "iteration": improve_used,
            "max_iterations": max_imp,
            "loop_round": int(job.current_round_no or 0),
            "message": (
                f"Gold 优化进行中（轮次 {int(job.current_round_no or 0)}，"
                f"已用改进 {improve_used}/{max_imp}；上限固定；"
                "初始 Prompt 不计入次数）"
            ),
        },
        loop_id=loop_id,
    )
    _commit(db, job)

    last_pv = None
    gold_passed = False

    # 硬上限：最多 max_imp 次 improve + (max_imp+1) 次 evaluate，防止异常无限循环
    max_eval_steps = max_imp + 2
    eval_steps = 0

    while eval_steps < max_eval_steps:
        eval_steps += 1

        # 人工中止：立即退出，不覆盖 ABORTED，保留当前迭代
        if _aborted(job):
            gold_passed = False
            _append_gold_log(
                job,
                {
                    "step": "aborted",
                    "iteration": improve_used,
                    "message": (
                        f"已中止 Gold 优化（迭代保持 {improve_used}/{max_imp}）"
                    ),
                },
                loop_id=loop_id,
            )
            _end_gold_loop_flag(job, loop_id)
            # 若尚未落地 ABORTED（竞态），补一次
            if job.status != JobStatus.ABORTED.value:
                from app.services.abort_service import finalize_abort

                finalize_abort(db, job, reason="Gold 优化过程中中止")
            else:
                _commit(db, job)
            break

        # 不 refresh 整对象（会把未 flush 的计数冲掉）；只检查同 session 上的预算状态
        if job.status == JobStatus.BUDGET_EXCEEDED.value:
            break

        # 若被更新的 re-annotate 抢占 loop_id，立即退出
        cur_loop = (job.progress or {}).get("gold_loop_id")
        if cur_loop and cur_loop != loop_id:
            gold_passed = False
            break

        _persist("gold_evaluating", improve_used)
        _append_gold_log(
            job,
            {
                "step": "evaluating",
                "iteration": improve_used,
                "message": f"Gold 评估中（已用改进 {improve_used}/{max_imp}）…",
            },
            loop_id=loop_id,
        )
        _commit(db, job)

        pv, metrics = evaluate_active_prompt_on_gold(
            db,
            job,
            log=True,
            iteration=improve_used,
            loop_id=loop_id,
        )
        last_pv = pv
        # 评测后再次钉死迭代，防止 on_usage 中间 commit 造成回跳
        improve_used = _sync_gold_iteration(
            job, improve_used, loop_id=loop_id, allow_decrease=False
        )
        _commit(db, job)

        acc = float(metrics.get("accuracy") or 0.0)
        gold_passed = acc >= target

        # 评测后再次检查中止：中止则绝不能以「达标」退出
        if _aborted(job):
            gold_passed = False
            _append_gold_log(
                job,
                {
                    "step": "aborted",
                    "iteration": improve_used,
                    "accuracy": acc,
                    "message": (
                        f"评测后检测到中止，不进入全量"
                        f"（当前 {acc:.2%}，目标 {target:.0%}）"
                    ),
                },
                loop_id=loop_id,
            )
            _end_gold_loop_flag(job, loop_id)
            if job.status != JobStatus.ABORTED.value:
                from app.services.abort_service import finalize_abort

                finalize_abort(db, job, reason="Gold 优化过程中中止")
            else:
                _commit(db, job)
            break

        if gold_passed and not (always_improve_once and improve_used == 0):
            # 达标前最后一道中止检查
            if _aborted(job):
                gold_passed = False
                break
            _persist("gold_ready_inner", improve_used, accuracy=acc)
            _append_gold_log(
                job,
                {
                    "step": "gold_ready",
                    "iteration": improve_used,
                    "version": pv.version,
                    "accuracy": acc,
                    "message": (
                        f"Gold 达标（v{pv.version}，{acc:.2%}，"
                        f"改进 {improve_used}/{max_imp}）"
                    ),
                },
                loop_id=loop_id,
            )
            _commit(db, job)
            break

        # 已用满 max 次改进 → 必须停止（保证能触发人工介入）
        if improve_used >= max_imp:
            gold_passed = False
            improve_used = _sync_gold_iteration(
                job, max_imp, loop_id=loop_id, allow_decrease=False
            )
            _append_gold_log(
                job,
                {
                    "step": "gold_refine_stop",
                    "iteration": improve_used,
                    "version": pv.version,
                    "accuracy": acc,
                    "message": (
                        f"改进次数已达上限 {improve_used}/{max_imp}"
                        f"（当前 {acc:.2%} < 目标 {target:.0%}），"
                        "停止自动优化；请点「重新标注」开启下一轮 loop"
                        "（可不改提示词 / 无需 QC）"
                    ),
                },
                loop_id=loop_id,
            )
            _commit(db, job)
            break

        # 消耗 1 次：本地 +1 后立即落库（只增不减）
        improve_used += 1
        if improve_used > max_imp:
            improve_used = max_imp
        _persist("improving_prompt", improve_used, accuracy=acc)

        gold_fb = (
            f"{feedback}\n"
            f"[Gold 评测] accuracy={acc:.4f}, target={target:.4f}, "
            f"badcases={metrics.get('badcase_count')}, n={metrics.get('n')}, "
            f"macro_f1={metrics.get('macro_f1')}, improve_round={improve_used}/{max_imp}. "
            "请针对 badcase 与准确率不足处改写 Prompt。"
        ).strip()
        _append_gold_log(
            job,
            {
                "step": "improving",
                "iteration": improve_used,
                "from_version": pv.version,
                "accuracy": acc,
                "message": (
                    f"第 {improve_used}/{max_imp} 次改进：Gold 未达标（{acc:.2%}），"
                    f"质检大模型根据 Gold 结果改 Prompt…"
                ),
            },
            loop_id=loop_id,
        )
        _commit(db, job)

        try:
            check_budget(job, 500)
            improved = qc.improve_from_badcases(
                pv.prompt_text,
                metrics.get("badcases") or [],
                feedback=gold_fb,
                metrics=metrics,
            )
        except BudgetExceeded:
            _sync_gold_iteration(
                job, improve_used, loop_id=loop_id, allow_decrease=False
            )
            _commit(db, job)
            raise

        # LLM 调用后再次钉死迭代（tokens 已累加在 job 上，一并 commit）
        improve_used = _sync_gold_iteration(
            job, improve_used, loop_id=loop_id, allow_decrease=False
        )
        _commit(db, job)

        reason = (
            (improved.get("change_reason") or "").strip()
            or f"质检大模型根据 Gold 准确率 {acc:.2%} 优化提示词"
        )
        new_text = (improved.get("prompt_text") or "").strip()
        if not new_text or new_text == (pv.prompt_text or "").strip():
            _append_gold_log(
                job,
                {
                    "step": "prompt_improve_noop",
                    "iteration": improve_used,
                    "version": pv.version,
                    "message": (
                        f"第 {improve_used}/{max_imp} 次改进未改动 Prompt 正文，"
                        "继续本轮 loop（已计入次数）"
                    ),
                },
                loop_id=loop_id,
            )
            _commit(db, job)
            continue

        new_pv = create_version(
            db,
            job,
            new_text,
            change_reason=reason,
            parent_version=pv.version,
            qc_feedback_id=qc_feedback_id,
            improvement_suggestion=improved.get("improvement_suggestion"),
            activate=True,
            source="qc_llm_gold",
        )
        improve_used = _sync_gold_iteration(
            job, improve_used, loop_id=loop_id, allow_decrease=False
        )
        _append_gold_log(
            job,
            {
                "step": "prompt_improved",
                "iteration": improve_used,
                "from_version": pv.version,
                "version": new_pv.version,
                "change_reason": reason,
                "prev_accuracy": acc,
                "prompt_preview": (new_pv.prompt_text or "")[:400],
                "message": (
                    f"已保存 Prompt v{pv.version} → v{new_pv.version}"
                    f"（第 {improve_used}/{max_imp} 次改进）：{reason}"
                ),
            },
            loop_id=loop_id,
        )
        log_event(
            db,
            "prompt_improvement_generated",
            job_id=job.id,
            payload={
                "from_version": pv.version,
                "to_version": new_pv.version,
                "prev_accuracy": acc,
                "change_reason": reason,
                "improve_used": improve_used,
                "max_imp": max_imp,
                "loop_id": loop_id,
            },
        )
        _commit(db, job)
        last_pv = new_pv

    # 中止：保留当前迭代，不再改状态
    if _aborted(job) or job.status == JobStatus.ABORTED.value:
        _sync_gold_iteration(
            job, improve_used, loop_id=loop_id, allow_decrease=False
        )
        _end_gold_loop_flag(job, loop_id)
        if job.status != JobStatus.ABORTED.value:
            from app.services.abort_service import finalize_abort

            finalize_abort(db, job, reason="Gold 优化过程中中止")
        else:
            _commit(db, job)
        return last_pv or active_prompt(db, job.id), False

    # 退出：强制最终迭代（失败时至少钉在 min(improve_used, max)）
    final_used = min(max(int(improve_used), 0), max_imp)
    # 若循环因用满而停，必须是 max_imp
    if not gold_passed and final_used >= max_imp:
        final_used = max_imp
    _sync_gold_iteration(
        job, final_used, loop_id=loop_id, allow_decrease=False
    )
    _end_gold_loop_flag(job, loop_id)
    _commit(db, job)
    return last_pv or active_prompt(db, job.id), gold_passed


def run_gold_optimization(db: Session, job_id: int) -> Job:
    lock = _job_lock(job_id)
    if not lock.acquire(blocking=False):
        job = db.get(Job, job_id)
        if job:
            job.error_message = "Gold 优化已在进行中，请勿重复启动"
            _commit(db, job)
        return job  # type: ignore[return-value]

    try:
        return _run_gold_optimization_locked(db, job_id)
    finally:
        lock.release()


def _run_gold_optimization_locked(db: Session, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise ValueError("job not found")

    gold_count = (
        db.query(GoldTestItem).filter(GoldTestItem.job_id == job.id).count()
    )
    if gold_count == 0:
        raise ValueError("Upload initial gold test set first")

    max_imp = begin_new_gold_loop(job, clear_gold_log=True)
    set_status(
        job,
        JobStatus.GOLD_OPTIMIZING,
        stage="gold_optimizing",
        gold_iteration=0,
        max_gold_iterations=max_imp,
        loop_round=int(job.current_round_no or 0),
        gold_loop_active=True,
    )
    # begin 允许清零
    _sync_gold_iteration(job, 0, allow_decrease=True)
    _commit(db, job)

    qc = QCAgent(
        job.label_schema or {},
        job.policy_rules or "",
        on_usage=lambda n: _safe_add_no_commit(job, n),
    )

    try:
        pv = active_prompt(db, job.id)
        if not pv:
            check_budget(job, 500)
            _sync_gold_iteration(job, 0, allow_decrease=True)
            set_status(
                job,
                JobStatus.GOLD_OPTIMIZING,
                stage="designing_initial_prompt",
                gold_iteration=0,
            )
            _append_gold_log(
                job,
                {
                    "step": "designing_initial_prompt",
                    "iteration": 0,
                    "message": "质检大模型正在根据细则设计初始 Prompt（不计入迭代次数）…",
                },
            )
            _commit(db, job)

            designed = qc.design_initial_prompt(
                seed_prompt=(job.policy_rules or "")
            )
            pv = create_version(
                db,
                job,
                designed["prompt_text"],
                change_reason=(
                    (designed.get("change_reason") or "").strip()
                    or "质检大模型根据细则生成初始标注 Prompt"
                ),
                activate=True,
                source="qc_llm_initial",
            )
            _sync_gold_iteration(job, 0, allow_decrease=True)
            _append_gold_log(
                job,
                {
                    "step": "prompt_created",
                    "version": pv.version,
                    "iteration": 0,
                    "change_reason": designed.get("change_reason"),
                    "prompt_preview": (pv.prompt_text or "")[:400],
                    "message": f"已生成初始 Prompt v{pv.version}（不计迭代次数）",
                },
            )
            _commit(db, job)

        _pv, gold_passed = refine_prompt_with_gold(
            db,
            job,
            feedback="",
            max_improve_rounds=_job_max_gold_iterations(job),
            reset_iteration=False,
        )

        # 中止：refine 已处理或需保持 ABORTED — 绝不能落 GOLD_READY
        if _aborted(job) or job.status == JobStatus.ABORTED.value:
            if job.status != JobStatus.ABORTED.value:
                from app.services.abort_service import finalize_abort

                finalize_abort(db, job, reason="Gold 优化过程中中止")
            return job

        # 最终迭代：以 refine 落库后的列为准，失败时若已达上限钉死 max
        final_iter = int(job.gold_iteration or 0)
        max_imp = _job_max_gold_iterations(job)
        acc = float((job.last_gold_metrics or {}).get("accuracy") or 0.0)
        target = float(job.target_accuracy or 0.0)
        # 硬校验：返回的 gold_passed 必须与真实 accuracy 一致
        if gold_passed and acc < target:
            gold_passed = False
        if gold_passed and _aborted(job):
            gold_passed = False
        if gold_passed:
            _sync_gold_iteration(job, final_iter, allow_decrease=False)
            _end_gold_loop_flag(job)
            set_status(
                job,
                JobStatus.GOLD_READY,
                stage="gold_ready",
                accuracy=acc,
                gold_iteration=final_iter,
                gold_loop_active=False,
            )
            _append_gold_log(
                job,
                {
                    "step": "gold_ready",
                    "accuracy": acc,
                    "iteration": final_iter,
                    "message": "Gold 调试达标，可进入全量标注…",
                },
            )
            log_event(
                db,
                "gold.ready",
                job_id=job.id,
                payload={"accuracy": acc, "iteration": final_iter},
            )
        else:
            if final_iter < max_imp and acc < float(job.target_accuracy or 0):
                # 异常提前退出时也要能人工介入：钉到当前 used，若已跑满则 max
                pass
            # 未达标：必须停在 GOLD_FAILED，迭代保持最终值（通常 max/max）
            final_iter = min(max(final_iter, 0), max_imp)
            _sync_gold_iteration(job, final_iter, allow_decrease=False)
            _end_gold_loop_flag(job)
            set_status(
                job,
                JobStatus.GOLD_FAILED,
                stage="gold_failed_await_human",
                accuracy=acc,
                gold_iteration=final_iter,
                gold_loop_active=False,
                phase="gold",
            )
            job.error_message = (
                f"Gold 未达目标 accuracy={acc:.4f} < {job.target_accuracy}；"
                f"已用改进 {final_iter}/{max_imp}。"
                f"请点「重新标注」开启下一轮 loop（可不改提示词 / 无需 QC）"
            )
            _append_gold_log(
                job,
                {
                    "step": "gold_failed_await_human",
                    "accuracy": acc,
                    "target_accuracy": job.target_accuracy,
                    "iteration": final_iter,
                    "max_iterations": max_imp,
                    "message": (
                        f"Gold 未达标（{acc:.2%} < "
                        f"{float(job.target_accuracy):.0%}），"
                        f"迭代保持 {final_iter}/{max_imp}；"
                        "点「重新标注」后才会清零并进入下一轮"
                    ),
                },
            )
        _commit(db, job)
        return job

    except BudgetExceeded:
        db.refresh(job)
        job.status = JobStatus.BUDGET_EXCEEDED.value
        _end_gold_loop_flag(job)
        _append_gold_log(
            job, {"step": "budget_exceeded", "message": "Token 预算已超出"}
        )
        _commit(db, job)
        return job
    except Exception as exc:  # noqa: BLE001
        job.status = JobStatus.FAILED.value
        job.error_message = str(exc)
        _end_gold_loop_flag(job)
        _append_gold_log(
            job, {"step": "error", "message": str(exc)}
        )
        _commit(db, job)
        raise


def _safe_add_no_commit(job: Job, n: int) -> None:
    """仅累加 token，不 commit——避免 Gold 循环中频繁 expire 导致迭代回跳。"""
    from app.services.budget import add_tokens

    add_tokens(job, n)


def _safe_add(db: Session, job: Job, n: int) -> None:
    """兼容旧调用：累加后 commit。"""
    _safe_add_no_commit(job, n)
    try:
        db.add(job)
        _commit(db, job)
    except BudgetExceeded:
        db.add(job)
        _commit(db, job)
        raise
