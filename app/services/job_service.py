"""Job lifecycle service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import AnnotationRecord, GoldTestItem, Job, PromptTemplate, PromptVersion  # noqa: F401
from app.schemas import JobCreate, JobOut
from app.services.events import log_event
from app.services.labeling import default_label_schema, get_threshold, is_threshold_set
from app.state_machine import JobStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# 任务类型中文名（与前端一致）
JOB_TYPE_LABELS = {
    "annotation": "数据标注",
    "prompt_debug": "Prompt调试",
    "data_clean": "数据清洗",
    "data_search": "数据库检索",
    "data_generate": "数据生成",
    "llm_mine": "大模型挖掘",
}


def normalize_job_type(raw: str | None) -> str:
    t = (raw or "annotation").strip().lower().replace("-", "_")
    if t not in JOB_TYPE_LABELS:
        raise ValueError(
            f"不支持的 job_type={raw}；可选：{', '.join(JOB_TYPE_LABELS)}"
        )
    return t


def create_job(db: Session, body: JobCreate) -> Job:
    # 判定阈值在全量标注后再设；创建时 threshold_set=False
    label_schema = default_label_schema(threshold_set=False)
    job_type = normalize_job_type(getattr(body, "job_type", None) or "annotation")

    policy = (body.policy_rules or "").strip()
    if job_type == "annotation" and not policy:
        raise ValueError("policy_rules is required（风控细则与判定说明）")
    if not policy:
        # 非标注类型可简化创建，写入占位说明便于后续恢复识别
        # 提示词调试：不把「（任务）名称」塞进 Prompt，仅作 policy 占位
        if job_type == "prompt_debug":
            policy = (body.name or "提示词调试").strip()
        else:
            policy = f"（{JOB_TYPE_LABELS.get(job_type, job_type)}任务）{body.name}"

    # 细则与初始 Prompt 合并：优先模板 / 显式 initial_prompt，否则细则本身即 Prompt v1
    # 提示词调试：默认空白 Prompt，由用户在调试台自行填写（避免预填占位文案）
    if job_type == "prompt_debug":
        if body.initial_prompt is not None:
            seed_prompt = (body.initial_prompt or "").strip()
        else:
            seed_prompt = ""
    else:
        seed_prompt = (body.initial_prompt or "").strip() or policy
    if body.template_id:
        tmpl = db.get(PromptTemplate, body.template_id)
        if tmpl:
            seed_prompt = tmpl.prompt_text
            # 模板也回填为细则依据（用户未另写细则时）
            if policy in ("", "（从模板）") or policy.startswith("（"):
                policy = tmpl.prompt_text
            tmpl.usage_count = int(tmpl.usage_count or 0) + 1

    job = Job(
        name=body.name,
        job_type=job_type,
        status=JobStatus.CREATED.value,
        label_schema=label_schema,
        policy_rules=policy,
        target_accuracy=body.target_accuracy,
        max_gold_iterations=body.max_gold_iterations,
        # 用户不配置预算：0 = 不限制（budget 服务会跳过硬停）
        token_budget=int(body.token_budget) if body.token_budget else 0,
        template_id=body.template_id,
        progress={
            "stage": "created",
            "job_type": job_type,
            "mode": "confidence_threshold"
            if job_type == "annotation"
            else job_type,
            "threshold_set": False,
            "prompt_seed": "merged_policy_rules",
        },
    )
    db.add(job)
    db.flush()

    # 始终写入 Prompt v1 = 合并后的种子文案（提示词调试可为空白）
    if job_type == "annotation":
        seed_reason = "seed from policy_rules (细则与初始 Prompt 合并)"
    elif job_type == "prompt_debug":
        custom_reason = (getattr(body, "seed_change_reason", None) or "").strip()
        if custom_reason:
            seed_reason = custom_reason
        elif seed_prompt:
            seed_reason = "初始版本"
        else:
            seed_reason = "empty seed for prompt debug"
    else:
        seed_reason = f"seed for {job_type} job"
    db.add(
        PromptVersion(
            job_id=job.id,
            version=1,
            prompt_text=seed_prompt,
            parent_version=None,
            change_reason=seed_reason,
            is_active=True,
        )
    )

    log_event(
        db,
        "job.created",
        job_id=job.id,
        payload={"name": job.name, "job_type": job_type},
    )
    db.commit()
    db.refresh(job)
    return job


def list_jobs(db: Session) -> list[Job]:
    return db.query(Job).order_by(Job.id.desc()).all()


def get_job(db: Session, job_id: int) -> Job | None:
    return db.get(Job, job_id)


def update_job_name(db: Session, job: Job, name: str) -> Job:
    """更新任务名称（trim 后非空）。"""
    trimmed = (name or "").strip()
    if not trimmed:
        raise ValueError("任务名称不能为空")
    if len(trimmed) > 256:
        raise ValueError("任务名称过长（最多 256 字）")
    if job.name == trimmed:
        return job
    old = job.name
    job.name = trimmed
    # 提示词调试：policy 仅作占位时与名称同步，避免列表/恢复识别漂移
    if (job.job_type or "") == "prompt_debug":
        pol = (job.policy_rules or "").strip()
        if not pol or pol == (old or "").strip():
            job.policy_rules = trimmed
    log_event(
        db,
        "job.renamed",
        job_id=job.id,
        payload={"from": old, "to": trimmed},
    )
    db.commit()
    db.refresh(job)
    return job


# 仅允许在「首次开跑前」或「可重新标注」时改 Gold 参数；loop 进行中禁止
_GOLD_PARAMS_EDITABLE_STATUSES = frozenset(
    {
        JobStatus.CREATED.value,
        JobStatus.GOLD_FAILED.value,
        JobStatus.GOLD_READY.value,
        JobStatus.AWAIT_DECISION_THRESHOLD.value,
        JobStatus.AWAIT_CONFIDENCE_BINS.value,
        JobStatus.AWAIT_QC.value,
        JobStatus.AWAIT_DECISION.value,
        JobStatus.ABORTED.value,
        JobStatus.COMPLETED.value,
        JobStatus.FAILED.value,
    }
)


def can_edit_gold_params(job: Job) -> bool:
    """Gold 目标准确率 / 最大迭代：仅首次开跑与重新标注边界可改。"""
    return (job.status or "") in _GOLD_PARAMS_EDITABLE_STATUSES


def update_gold_params(
    db: Session,
    job: Job,
    *,
    target_accuracy: float | None = None,
    max_gold_iterations: int | None = None,
) -> Job:
    """
    更新 Job 的 Gold 目标准确率 / 最大迭代次数。
    至少一项非 None；loop 进行中（优化/标注中）拒绝修改。
    """
    if target_accuracy is None and max_gold_iterations is None:
        raise ValueError("请至少提供 target_accuracy 或 max_gold_iterations")
    if not can_edit_gold_params(job):
        raise ValueError(
            f"当前状态「{job.status}」不可修改 Gold 参数；"
            "仅首次开始标注前，或点「重新标注」开启下一轮前可改"
        )

    changed: dict = {}
    if target_accuracy is not None:
        v = float(target_accuracy)
        if not (0.0 <= v <= 1.0):
            raise ValueError("target_accuracy 须在 0~1 之间")
        job.target_accuracy = v
        changed["target_accuracy"] = v

    if max_gold_iterations is not None:
        n = int(max_gold_iterations)
        if n < 1 or n > 50:
            raise ValueError("max_gold_iterations 须在 1~50 之间")
        job.max_gold_iterations = n
        changed["max_gold_iterations"] = n

    prog = dict(job.progress or {})
    if "target_accuracy" in changed:
        prog["target_accuracy"] = changed["target_accuracy"]
    if "max_gold_iterations" in changed:
        prog["max_gold_iterations"] = changed["max_gold_iterations"]
    job.progress = prog

    log_event(
        db,
        "job.gold_params_updated",
        job_id=job.id,
        payload=changed,
    )
    db.commit()
    db.refresh(job)
    return job


def delete_job(db: Session, job_id: int) -> bool:
    """
    永久删除 Job 及其关联数据（标注、Gold、轮次、QC、Prompt 版本、事件日志等）。
    返回是否找到并删除。
    """
    from app.models import (
        AnnotationRecord,
        EventLog,
        GoldTestItem,
        PromptVersion,
        QCFeedback,
        QCSample,
        Round,
    )

    job = db.get(Job, job_id)
    if not job:
        return False

    # 先清有 round_id / job_id 的子表，避免 SQLite FK 约束失败
    round_ids = [
        r.id
        for r in db.query(Round.id).filter(Round.job_id == job_id).all()
    ]
    if round_ids:
        db.query(QCSample).filter(QCSample.round_id.in_(round_ids)).delete(
            synchronize_session=False
        )
        db.query(QCFeedback).filter(QCFeedback.round_id.in_(round_ids)).delete(
            synchronize_session=False
        )
    db.query(QCSample).filter(QCSample.job_id == job_id).delete(
        synchronize_session=False
    )
    db.query(QCFeedback).filter(QCFeedback.job_id == job_id).delete(
        synchronize_session=False
    )
    db.query(Round).filter(Round.job_id == job_id).delete(synchronize_session=False)
    db.query(AnnotationRecord).filter(AnnotationRecord.job_id == job_id).delete(
        synchronize_session=False
    )
    db.query(GoldTestItem).filter(GoldTestItem.job_id == job_id).delete(
        synchronize_session=False
    )
    db.query(PromptVersion).filter(PromptVersion.job_id == job_id).delete(
        synchronize_session=False
    )
    db.query(EventLog).filter(EventLog.job_id == job_id).delete(
        synchronize_session=False
    )

    db.delete(job)
    db.commit()

    # 清理导出缓存与中止信号（忽略失败）
    try:
        from config import EXPORT_DIR

        for p in EXPORT_DIR.glob(f"job_{job_id}_*"):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.services.abort_service import clear_abort_request

        clear_abort_request(job_id, None)
    except Exception:  # noqa: BLE001
        pass
    return True


def delete_jobs(db: Session, job_ids: list[int]) -> dict[str, Any]:
    """批量永久删除。返回 {deleted: [...], missing: [...]}。"""
    deleted: list[int] = []
    missing: list[int] = []
    seen: set[int] = set()
    for raw in job_ids:
        try:
            jid = int(raw)
        except (TypeError, ValueError):
            continue
        if jid in seen:
            continue
        seen.add(jid)
        if delete_job(db, jid):
            deleted.append(jid)
        else:
            missing.append(jid)
    return {"deleted": deleted, "missing": missing, "count": len(deleted)}


def job_to_out(db: Session, job: Job) -> JobOut:
    ann_count = (
        db.query(func.count(AnnotationRecord.id))
        .filter(AnnotationRecord.job_id == job.id)
        .scalar()
        or 0
    )
    gold_count = (
        db.query(func.count(GoldTestItem.id))
        .filter(GoldTestItem.job_id == job.id)
        .scalar()
        or 0
    )
    schema = job.label_schema or {}
    jt = (getattr(job, "job_type", None) or "annotation") or "annotation"
    return JobOut(
        id=job.id,
        name=job.name,
        job_type=jt,
        status=job.status,
        label_schema=schema,
        policy_rules=job.policy_rules or "",
        decision_threshold=get_threshold(schema),
        threshold_set=is_threshold_set(schema),
        target_accuracy=job.target_accuracy,
        max_gold_iterations=job.max_gold_iterations,
        token_budget=job.token_budget,
        tokens_used=job.tokens_used or 0,
        confidence_bins=job.confidence_bins,
        average_rounds=job.average_rounds,
        current_round_no=job.current_round_no or 0,
        gold_iteration=job.gold_iteration or 0,
        last_gold_metrics=job.last_gold_metrics,
        progress=job.progress,
        error_message=job.error_message,
        annotation_count=int(ann_count),
        gold_count=int(gold_count),
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def set_status(job: Job, status: JobStatus | str, **progress: Any) -> None:
    new_status = status.value if isinstance(status, JobStatus) else str(status)
    # 中止冻结：ABORTED / 中止信号期间，禁止后台任务改写状态（否则 UI 中止后又被刷成别的状态）
    # 仅允许：保持 ABORTED，或人工重开 loop（GOLD_OPTIMIZING / PROMPT_IMPROVING）
    try:
        from app.services.abort_service import is_abort_requested

        frozen = is_abort_requested(job) or job.status == "ABORTED"
        if frozen:
            allowed_while_aborted = {
                "ABORTED",
                "GOLD_OPTIMIZING",
                "PROMPT_IMPROVING",
            }
            if new_status not in allowed_while_aborted:
                return
    except Exception:  # noqa: BLE001
        pass

    job.status = new_status
    job.updated_at = _utcnow()
    if progress:
        # gold_iteration：同一优化过程中默认单调不减，防止并发/过期会话把 3 写回 2
        if "gold_iteration" in progress:
            try:
                incoming = max(0, int(progress["gold_iteration"]))
            except (TypeError, ValueError):
                incoming = int(job.gold_iteration or 0)
            allow_dec = bool(progress.pop("allow_gold_decrease", False))
            stage = str(progress.get("stage") or "")
            # 仅显式允许，或新 loop 排队/启动阶段，才允许降到 0
            if allow_dec or stage in {
                "queued_reopen_gold_loop",
                "human_reopen_gold_loop",
                "gold_optimizing",
                "gold_loop_start",
                "pipeline_started",
                "designing_initial_prompt",
            }:
                job.gold_iteration = incoming
            else:
                # 优化过程中只允许不减（1→2→3），禁止 3→2 / 2→1
                job.gold_iteration = max(int(job.gold_iteration or 0), incoming)
        base = dict(job.progress or {})
        # 上限固定为 job 配置
        if "max_gold_iterations" in progress or "gold_iteration" in progress:
            progress = {
                **progress,
                "max_gold_iterations": max(1, int(job.max_gold_iterations or 3)),
            }
            if "gold_iteration" in progress:
                progress["gold_iteration"] = int(job.gold_iteration or 0)
                progress["iteration"] = int(job.gold_iteration or 0)
        base.update(progress)
        job.progress = base


def active_prompt(db: Session, job_id: int) -> PromptVersion | None:
    return (
        db.query(PromptVersion)
        .filter(PromptVersion.job_id == job_id, PromptVersion.is_active.is_(True))
        .order_by(PromptVersion.version.desc())
        .first()
    )
