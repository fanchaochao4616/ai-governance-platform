"""Full / subset labeling and gold evaluation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.agents.annotator import AnnotatorAgent
from app.models import AnnotationRecord, GoldTestItem, Job, PromptVersion, Round
from app.services.budget import BudgetExceeded, add_tokens, check_budget
from app.services.confidence import in_named_ranges, multi_round_average
from app.services.events import log_event
from app.services.job_service import active_prompt, set_status
from app.services.labeling import (
    derive_label,
    get_threshold,
    is_threshold_set,
    set_threshold_on_schema,
)
from app.services.metrics_calc import evaluate_predictions
from app.state_machine import JobStatus, RoundStatus
from config import ANNOTATOR_CONCURRENCY, DEFAULT_DECISION_THRESHOLD


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _usage_cb(
    db: Session,
    job: Job,
    *,
    commit: bool = False,
) -> Callable[[int], None]:
    """
    Token 累加回调。
    默认不 commit：Gold 循环里频繁 commit 会导致 session expire，
    gold_iteration 被旧快照写回（出现 3→2 / 2→1 跳变）。
    全量标注等需要即时落库时再传 commit=True。
    """

    def _on(n: int) -> None:
        try:
            add_tokens(job, n)
            if commit:
                db.add(job)
                db.commit()
        except BudgetExceeded:
            if commit:
                db.add(job)
                db.commit()
            raise

    return _on


def evaluate_on_gold(
    db: Session,
    job: Job,
    prompt_text: str,
) -> dict[str, Any]:
    gold_items = (
        db.query(GoldTestItem).filter(GoldTestItem.job_id == job.id).all()
    )
    if not gold_items:
        raise ValueError("Gold test set is empty")

    agent = AnnotatorAgent(
        job.label_schema or {},
        job.policy_rules or "",
        prompt_text,
        few_shots=[
            {"text": g.text, "label": g.gold_label}
            for g in gold_items[:5]
        ],
        on_usage=_usage_cb(db, job),
    )

    pairs: list[tuple[str, str, str]] = []
    details: list[dict[str, Any]] = []
    # Gold 评估用内部默认阈值，不要求用户已设置判定阈值
    gold_th = DEFAULT_DECISION_THRESHOLD
    for g in gold_items:
        check_budget(job, estimate=200)
        # Never pass seq to agent
        result = agent.annotate(g.text, force_threshold=gold_th)
        conf = float(result.get("confidence") or 0.0)
        pred = result["label"] or derive_label(
            conf, job.label_schema, threshold=gold_th
        )
        pairs.append((g.text, g.gold_label, pred))
        details.append(
            {
                "id": g.id,
                "text": g.text,
                "gold_label": g.gold_label,
                "pred_label": pred,
                "confidence": round(conf, 6),
                "correct": str(g.gold_label) == str(pred),
                "reasoning": (result.get("reasoning") or "")[:500],
            }
        )

    metrics = evaluate_predictions(pairs)
    metrics["gold_eval_threshold"] = gold_th
    # 供 UI 展开：Gold 原文、金标、小模型预测与置信度
    metrics["details"] = details
    return metrics


def run_labeling_batch(
    db: Session,
    job: Job,
    records: list[AnnotationRecord],
    prompt_version: PromptVersion,
    round_no: int,
) -> int:
    """Label records; append round results. Returns success count."""
    agent = AnnotatorAgent(
        job.label_schema or {},
        job.policy_rules or "",
        prompt_version.prompt_text,
        on_usage=_usage_cb(db, job),
    )

    def _work(rec_id: int, text: str) -> tuple[int, dict[str, Any] | Exception]:
        try:
            return rec_id, agent.annotate(text)
        except Exception as exc:  # noqa: BLE001
            return rec_id, exc

    success = 0
    # Process in chunks to commit progress
    chunk_size = 20
    for start in range(0, len(records), chunk_size):
        from app.services.abort_service import is_abort_requested

        if job.status == JobStatus.BUDGET_EXCEEDED.value:
            break
        if is_abort_requested(job) or job.status == JobStatus.ABORTED.value:
            break
        chunk = records[start : start + chunk_size]
        # refresh job status（用于感知中止 / 预算）
        db.refresh(job)
        if is_abort_requested(job) or job.status == JobStatus.ABORTED.value:
            break
        if job.status == JobStatus.BUDGET_EXCEEDED.value:
            break
        results_map: dict[int, dict[str, Any] | Exception] = {}
        with ThreadPoolExecutor(max_workers=ANNOTATOR_CONCURRENCY) as pool:
            futs = {
                pool.submit(_work, rec.id, rec.text): rec.id for rec in chunk
            }
            for fut in as_completed(futs):
                rid, res = fut.result()
                results_map[rid] = res

        for rec in chunk:
            res = results_map.get(rec.id)
            if res is None or isinstance(res, Exception):
                continue
            rounds = list(rec.rounds or [])
            # remove existing same round if re-run
            rounds = [r for r in rounds if int(r.get("round", -1)) != round_no]
            rounds.append(
                {
                    "round": round_no,
                    "label": res["label"],
                    "confidence": res["confidence"],
                    "reasoning": res.get("reasoning", ""),
                    "prompt_version_id": prompt_version.id,
                    "model": res.get("model"),
                    "created_at": _utcnow_iso(),
                }
            )
            rec.rounds = rounds
            rec.current_label = res["label"]
            rec.current_confidence = res["confidence"]
            success += 1

        total = len(records)
        pct = round(100.0 * success / total, 2) if total else 0.0
        from app.services.abort_service import (
            commit_respecting_abort,
            is_abort_requested,
        )

        if is_abort_requested(job) or job.status == JobStatus.ABORTED.value:
            commit_respecting_abort(db, job)
            break
        job.progress = {
            **(job.progress or {}),
            "phase": "full_label",
            "pipeline": "annotation",
            "labeled": success,
            "label_target": total,
            "label_percent": pct,
            "round_no": round_no,
            "full_label_message": f"全量标注进度：{success}/{total}（{pct}%）",
        }
        commit_respecting_abort(db, job)

    return success


def start_full_or_subset_round(
    db: Session,
    job: Job,
    *,
    target_ranges: list[str] | None = None,
    bins: list[dict[str, Any]] | None = None,
) -> Round:
    """
    Create a new round and label either all records (round 1 / full)
    or only those whose previous confidence falls in target_ranges.
    """
    pv = active_prompt(db, job.id)
    if not pv:
        raise ValueError("No active prompt version")

    # 硬闸门：Gold 未达标 / 已中止 时禁止全量或子集标注
    from app.services.abort_service import can_start_full_label, is_abort_requested

    if is_abort_requested(job) or job.status == JobStatus.ABORTED.value:
        raise ValueError("任务已中止，禁止全量标注；请重新标注开启新 loop")
    ok, gate_reason = can_start_full_label(job)
    if not ok:
        raise ValueError(gate_reason)

    # 当前轮次由 begin_new_gold_loop 在「重新标注/开跑」时 +1；
    # 全量标注沿用该轮次，避免再 +1 导致轮次跳号。
    # 若同号 Round 已存在，则再 +1 以支持同 loop 内多次标注。
    round_no = max(1, int(job.current_round_no or 0))
    existing = (
        db.query(Round)
        .filter(Round.job_id == job.id, Round.round_no == round_no)
        .first()
    )
    if existing is not None:
        round_no = round_no + 1
        job.current_round_no = round_no

    q = db.query(AnnotationRecord).filter(AnnotationRecord.job_id == job.id)
    all_recs = q.order_by(AnnotationRecord.seq.asc()).all()
    if not all_recs:
        raise ValueError("No dataset imported")

    if target_ranges and bins and round_no > 1:
        to_label = [
            r
            for r in all_recs
            if in_named_ranges(r.current_confidence, bins, target_ranges)
        ]
    else:
        to_label = all_recs
        target_ranges = None

    rnd = Round(
        job_id=job.id,
        round_no=round_no,
        status=RoundStatus.LABELING.value,
        prompt_version_id=pv.id,
        target_ranges_for_labeling=target_ranges,
        labeled_count=0,
    )
    db.add(rnd)
    job.current_round_no = round_no
    set_status(job, JobStatus.ROUND_LABELING, stage="labeling", round_no=round_no)
    db.commit()
    db.refresh(rnd)

    try:
        count = run_labeling_batch(db, job, to_label, pv, round_no)
        db.refresh(job)
        if job.status == JobStatus.BUDGET_EXCEEDED.value:
            rnd.status = RoundStatus.FAILED.value
            rnd.labeled_count = count
            db.commit()
            return rnd

        from app.services.abort_service import finalize_abort, is_abort_requested

        if is_abort_requested(job) or job.status == JobStatus.ABORTED.value:
            rnd.labeled_count = count
            rnd.status = RoundStatus.FAILED.value
            if job.status != JobStatus.ABORTED.value:
                finalize_abort(db, job, reason="全量标注过程中中止")
            else:
                db.commit()
            return rnd

        rnd.labeled_count = count
        # 全量/子集标注完成后：先设判定阈值，再分层 QC
        rnd.status = RoundStatus.AWAIT_THRESHOLD.value
        set_status(
            job,
            JobStatus.AWAIT_DECISION_THRESHOLD,
            stage="await_decision_threshold",
            round_no=round_no,
            labeled=count,
        )
        log_event(
            db,
            "round.completed",
            job_id=job.id,
            payload={"round_no": round_no, "labeled": count},
        )
        db.commit()
    except BudgetExceeded:
        rnd.status = RoundStatus.FAILED.value
        db.commit()
    except Exception as exc:  # noqa: BLE001
        rnd.status = RoundStatus.FAILED.value
        job.status = JobStatus.FAILED.value
        job.error_message = str(exc)
        db.commit()
        raise

    return rnd


def apply_multi_round_average(
    db: Session,
    job: Job,
    *,
    from_round: int = 1,
    to_round: int | None = None,
    selected_rounds: list[int] | None = None,
    mark_completed: bool = False,
) -> int:
    """
    多轮置信度/标签平均。
    默认不把任务标成 COMPLETED（分层抽 QC 时也会调用，完成后仍应可「重新标注」）。
    仅 mark_completed=True 时标记已完成。
    """
    if selected_rounds is None:
        end = to_round or int(job.current_round_no or 1)
        selected_rounds = list(range(from_round, end + 1))

    job.average_rounds = {
        "from": from_round,
        "to": to_round or max(selected_rounds) if selected_rounds else 1,
        "selected": selected_rounds,
    }
    records = (
        db.query(AnnotationRecord)
        .filter(AnnotationRecord.job_id == job.id)
        .all()
    )
    updated = 0
    for rec in records:
        label, conflict = multi_round_average(rec.rounds or [], selected_rounds)
        rec.final_label = label
        rec.conflict = conflict
        updated += 1

    log_event(
        db,
        "multi_round_average_applied",
        job_id=job.id,
        payload={
            "selected_rounds": selected_rounds,
            "count": updated,
            "mark_completed": mark_completed,
        },
    )
    if mark_completed:
        set_status(job, JobStatus.COMPLETED, stage="completed")
    else:
        # 保持当前业务状态（如 AWAIT_QC），仅记录进度阶段
        prog = dict(job.progress or {})
        prog["stage"] = "multi_round_averaged"
        prog["multi_round_averaged"] = True
        job.progress = prog
    db.commit()
    return updated


def confidence_distribution(db: Session, job_id: int) -> dict[str, Any]:
    rows = (
        db.query(AnnotationRecord.current_confidence)
        .filter(
            AnnotationRecord.job_id == job_id,
            AnnotationRecord.current_confidence.isnot(None),
        )
        .all()
    )
    confs = [float(r[0]) for r in rows if r[0] is not None]
    buckets = [0] * 10
    for c in confs:
        idx = min(9, int(c * 10))
        if c >= 1.0:
            idx = 9
        buckets[idx] += 1
    return {
        "count": len(confs),
        "mean": (sum(confs) / len(confs)) if confs else 0.0,
        "histogram": [
            {"bin": f"{i/10:.1f}-{(i+1)/10:.1f}", "count": buckets[i]}
            for i in range(10)
        ],
        "values_sample": confs[:500],
    }


def apply_decision_threshold(
    db: Session,
    job: Job,
    threshold: float,
    *,
    round_no: int | None = None,
) -> dict[str, Any]:
    """
    在标注完成、用户查看置信度分布后设置判定阈值，
    批量将 current_confidence → label 1/0。
    """
    from app.services.abort_service import is_abort_requested

    if job.status == JobStatus.ABORTED.value or is_abort_requested(job):
        raise ValueError("任务已中止，无法应用阈值；请先点「重新标注」")

    th = max(0.0, min(1.0, float(threshold)))
    schema = set_threshold_on_schema(job.label_schema, th)
    job.label_schema = schema

    rn = round_no if round_no is not None else int(job.current_round_no or 0)
    records = (
        db.query(AnnotationRecord)
        .filter(AnnotationRecord.job_id == job.id)
        .all()
    )
    pos = neg = skipped = 0
    for rec in records:
        conf = rec.current_confidence
        if conf is None:
            skipped += 1
            continue
        label = derive_label(float(conf), schema, threshold=th)
        rec.current_label = label
        # 回写本轮 round 记录中的 label
        rounds = list(rec.rounds or [])
        updated = False
        new_rounds = []
        for r in rounds:
            r = dict(r)
            if rn and int(r.get("round", -1)) == rn:
                r["label"] = label
                r["decision_threshold"] = th
                updated = True
            new_rounds.append(r)
        if not updated and rounds:
            # 无 round 匹配则更新最后一轮
            last = dict(new_rounds[-1])
            last["label"] = label
            last["decision_threshold"] = th
            new_rounds[-1] = last
        rec.rounds = new_rounds
        if label == schema.get("positive_label"):
            pos += 1
        else:
            neg += 1

    # round status
    from app.models import Round

    rnd = (
        db.query(Round)
        .filter(Round.job_id == job.id, Round.round_no == rn)
        .first()
        if rn
        else None
    )
    if rnd:
        rnd.status = RoundStatus.AWAIT_BINS.value
        rnd.metrics = {
            **(rnd.metrics or {}),
            "decision_threshold": th,
            "positive_count": pos,
            "negative_count": neg,
        }

    set_status(
        job,
        JobStatus.AWAIT_CONFIDENCE_BINS,
        stage="await_confidence_bins",
        decision_threshold=th,
        positive_count=pos,
        negative_count=neg,
    )
    log_event(
        db,
        "decision_threshold_applied",
        job_id=job.id,
        payload={"threshold": th, "positive": pos, "negative": neg, "skipped": skipped},
    )
    db.commit()
    return {
        "threshold": th,
        "positive_count": pos,
        "negative_count": neg,
        "skipped": skipped,
        "status": job.status,
    }
