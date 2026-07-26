"""Confidence bins definition, stratified QC, human review."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AnnotationRecord, Job, QCFeedback, QCSample, Round
from app.schemas import ConfidenceBinsRequest, QCSubmitRequest
from app.services.confidence import (
    recommend_bins,
    stratified_sample,
    validate_bins,
)
from app.services.events import log_event
from app.services.gold_service import add_qc_correction
from app.services.job_service import set_status
from app.state_machine import JobStatus, RoundStatus
from config import DEFAULT_QC_PER_BIN


def get_round(db: Session, job_id: int, round_no: int) -> Round | None:
    return (
        db.query(Round)
        .filter(Round.job_id == job_id, Round.round_no == round_no)
        .first()
    )


def reasoning_field_name(round_no: int) -> str:
    """与导出列一致：round1_reasoning / round2_reasoning …"""
    return f"round{int(round_no)}_reasoning"


def get_round_reasoning(
    rec: AnnotationRecord,
    round_no: int,
) -> tuple[str, int | None]:
    """
    取指定轮次的 reasoning（即导出中的 round{N}_reasoning）。
    返回 (text, actual_round_used)。
    """
    rounds = list(rec.rounds or [])
    if not rounds:
        return "", None
    want = int(round_no)
    by_rn: dict[int, dict[str, Any]] = {}
    for rr in rounds:
        try:
            rn = int(rr.get("round"))
        except (TypeError, ValueError):
            continue
        by_rn[rn] = rr
    if want in by_rn:
        return str(by_rn[want].get("reasoning") or ""), want
    # 回退：最后一轮
    last = rounds[-1]
    try:
        rn = int(last.get("round") or 0) or None
    except (TypeError, ValueError):
        rn = None
    return str(last.get("reasoning") or ""), rn


def define_bins_and_sample(
    db: Session,
    job: Job,
    round_no: int,
    body: ConfidenceBinsRequest,
) -> Round:
    if job.status == JobStatus.ABORTED.value:
        raise ValueError("任务已中止，无法应用分层并抽 QC；请先点「重新标注」")
    from app.services.abort_service import is_abort_requested

    if is_abort_requested(job):
        raise ValueError("任务已中止，无法应用分层并抽 QC；请先点「重新标注」")

    rnd = get_round(db, job.id, round_no)
    if not rnd:
        raise ValueError("round not found")
    if job.status not in {
        JobStatus.AWAIT_CONFIDENCE_BINS.value,
        JobStatus.AWAIT_QC.value,
        JobStatus.AWAIT_DECISION_THRESHOLD.value,
    }:
        # allow redefine while awaiting bins (must have applied threshold first ideally)
        if rnd.status not in {
            RoundStatus.AWAIT_BINS.value,
            RoundStatus.AWAIT_QC.value,
            RoundStatus.AWAIT_THRESHOLD.value,
        }:
            raise ValueError(f"round not ready for bins (status={rnd.status})")
    if job.status == JobStatus.AWAIT_DECISION_THRESHOLD.value:
        raise ValueError("请先设置判定阈值（全量标注后），再定义置信度分层")

    bins = [b.model_dump() for b in body.bins]
    msgs = validate_bins(bins)
    hard = [m for m in msgs if "invalid" in m or "duplicate" in m or "required" in m]
    if hard:
        raise ValueError("; ".join(hard))

    per_bin = body.qc_per_bin or DEFAULT_QC_PER_BIN
    records = (
        db.query(AnnotationRecord)
        .filter(AnnotationRecord.job_id == job.id)
        .all()
    )
    items = []
    for r in records:
        if r.current_confidence is None:
            continue
        reason, _rn = get_round_reasoning(r, round_no)
        items.append(
            {
                "seq": r.seq,
                "text": r.text,
                "pred_label": r.current_label,
                "confidence": r.current_confidence,
                # 对应导出 round{N}_reasoning
                "reasoning": reason,
            }
        )
    sampled = stratified_sample(items, bins, per_bin)
    # High→Low；同分 seq 升序
    sampled.sort(
        key=lambda x: (
            -(float(x["confidence"]) if x.get("confidence") is not None else -1.0),
            int(x.get("seq") or 0),
        )
    )

    # clear previous QC samples for this round
    db.query(QCSample).filter(QCSample.round_id == rnd.id).delete()
    for s in sampled:
        db.add(
            QCSample(
                round_id=rnd.id,
                job_id=job.id,
                seq=int(s["seq"]),
                text=s["text"],
                pred_label=s.get("pred_label"),
                confidence=s.get("confidence"),
                reasoning=(s.get("reasoning") or None),
                bin_name=s.get("bin_name") or "Medium",
            )
        )

    ranges_obj = {b["name"]: {"min": b["min"], "max": b["max"]} for b in bins}
    rnd.confidence_ranges = ranges_obj
    job.confidence_bins = ranges_obj
    rnd.status = RoundStatus.AWAIT_QC.value
    set_status(
        job,
        JobStatus.AWAIT_QC,
        stage="await_qc",
        qc_count=len(sampled),
        bin_warnings=msgs,
    )
    log_event(
        db,
        "confidence_range_defined",
        job_id=job.id,
        payload={"round_no": round_no, "bins": bins, "qc_count": len(sampled)},
    )
    db.commit()
    db.refresh(rnd)
    return rnd


def recommend_bins_for_job(db: Session, job_id: int) -> list[dict[str, Any]]:
    confs = [
        float(r[0])
        for r in db.query(AnnotationRecord.current_confidence)
        .filter(
            AnnotationRecord.job_id == job_id,
            AnnotationRecord.current_confidence.isnot(None),
        )
        .all()
        if r[0] is not None
    ]
    return recommend_bins(confs)


def list_qc_samples(db: Session, job_id: int, round_no: int) -> list[QCSample]:
    rnd = get_round(db, job_id, round_no)
    if not rnd:
        return []
    samples = (
        db.query(QCSample)
        .filter(QCSample.round_id == rnd.id)
        .order_by(QCSample.bin_name, QCSample.seq)
        .all()
    )
    # 从 AnnotationRecord.rounds 取 round{N}_reasoning 写回/补全
    seqs = [s.seq for s in samples]
    recs = {
        r.seq: r
        for r in db.query(AnnotationRecord)
        .filter(
            AnnotationRecord.job_id == job_id,
            AnnotationRecord.seq.in_(seqs) if seqs else False,
        )
        .all()
    } if seqs else {}
    dirty = False
    for s in samples:
        rec = recs.get(s.seq)
        if not rec:
            continue
        reason, _rn = get_round_reasoning(rec, round_no)
        if reason and (s.reasoning or "").strip() != reason.strip():
            s.reasoning = reason
            dirty = True
        elif reason and not (s.reasoning or "").strip():
            s.reasoning = reason
            dirty = True
    if dirty:
        db.commit()
    # 展示顺序：置信度 High→Low，同分 seq↑
    samples.sort(
        key=lambda s: (
            -(float(s.confidence) if s.confidence is not None else -1.0),
            int(s.seq or 0),
        )
    )
    return samples


def list_qc_samples_for_display(
    db: Session,
    job_id: int,
    round_no: int | None = None,
) -> tuple[list[QCSample], int]:
    """
    展示用 QC：优先指定轮次；若为空则回退到最近一轮仍有样本的 QC。
    中止后不丢上次抽检结果。返回 (samples, actual_round_no)。
    """
    want = int(round_no or 0)
    if want > 0:
        samples = list_qc_samples(db, job_id, want)
        if samples:
            return samples, want
    rounds = (
        db.query(Round)
        .filter(Round.job_id == job_id)
        .order_by(Round.round_no.desc())
        .all()
    )
    for rnd in rounds:
        n = (
            db.query(QCSample)
            .filter(QCSample.round_id == rnd.id)
            .count()
        )
        if n:
            rn = int(rnd.round_no)
            return list_qc_samples(db, job_id, rn), rn
    return [], want


def qc_sample_to_out(s: QCSample, round_no: int) -> dict[str, Any]:
    """序列化为 API 输出，附带 roundN_reasoning 字段名。"""
    rn = int(round_no)
    field = reasoning_field_name(rn)
    return {
        "id": s.id,
        "seq": s.seq,
        "text": s.text,
        "pred_label": s.pred_label,
        "confidence": s.confidence,
        "reasoning": s.reasoning,
        "reasoning_round": rn,
        "reasoning_field": field,
        "bin_name": s.bin_name,
        "human_label": s.human_label,
        "corrected": bool(s.corrected),
        "reviewed": bool(s.reviewed),
    }


def _qc_current_label(sample: QCSample) -> str:
    """当前 QC 样本上的 label 状态：已有 human 用 human，否则用 pred。"""
    if sample.human_label is not None and str(sample.human_label) != "":
        return str(sample.human_label)
    return str(sample.pred_label or "")


def submit_qc(
    db: Session,
    job: Job,
    round_no: int,
    body: QCSubmitRequest,
) -> dict[str, Any]:
    """
    按 id/seq 匹配 QC 样本：
    - 提交 label 与当前状态一致 → 忽略该项
    - 不一致 → 更新为提交的 label
    - 全部一致（无任何改动）→ 忽略本次提交并提示
    """
    rnd = get_round(db, job.id, round_no)
    if not rnd:
        raise ValueError("round not found")

    samples = {
        s.seq: s
        for s in db.query(QCSample).filter(QCSample.round_id == rnd.id).all()
    }

    # 按 id（seq）匹配：仅当提交 label ≠ 当前 label 时更新
    effective: list[tuple[Any, QCSample, str]] = []
    for rev in body.reviews or []:
        s = samples.get(rev.seq)
        if not s:
            continue
        current = _qc_current_label(s)
        submitted = str(rev.human_label if rev.human_label is not None else "")
        if submitted == current:
            continue
        effective.append((rev, s, current))

    if not effective:
        log_event(
            db,
            "qc.ignored_duplicate",
            job_id=job.id,
            payload={"round_no": round_no, "reason": "no_label_changes"},
        )
        db.commit()
        return {
            "ignored": True,
            "message": "忽略重复提交 QC：没有做任何标签修改",
            "agreement_rate": None,
            "reviewed": 0,
            "feedback_id": None,
        }

    total = 0
    for rev, s, _prev in effective:
        submitted = str(rev.human_label)
        s.human_label = submitted
        s.corrected = submitted != str(s.pred_label or "")
        s.reviewed = True
        total += 1
        if s.corrected and s.human_label:
            add_qc_correction(
                db,
                job.id,
                text=s.text,
                gold_label=s.human_label,
                round_id=rnd.id,
                seq=s.seq,
            )
            rec = (
                db.query(AnnotationRecord)
                .filter(
                    AnnotationRecord.job_id == job.id,
                    AnnotationRecord.seq == s.seq,
                )
                .first()
            )
            if rec:
                rec.current_label = s.human_label

    # 计算全量样本 agreement（含历史已审 + 本次更新）
    all_reviewed = [s for s in samples.values() if s.reviewed]
    if all_reviewed:
        agree_all = sum(
            1 for s in all_reviewed if str(s.human_label) == str(s.pred_label or "")
        )
        agreement = agree_all / len(all_reviewed)
    else:
        agreement = None

    fb = QCFeedback(
        job_id=job.id,
        round_id=rnd.id,
        feedback_text=body.feedback_text or "",
        agreement_rate=agreement,
    )
    db.add(fb)
    rnd.metrics = {
        **(rnd.metrics or {}),
        "qc_agreement": agreement,
        "qc_reviewed": sum(1 for s in samples.values() if s.reviewed),
        "qc_corrected": sum(1 for s in samples.values() if s.corrected),
        "qc_last_submit_changes": total,
    }
    rnd.status = RoundStatus.AWAIT_DECISION.value
    set_status(
        job,
        JobStatus.AWAIT_DECISION,
        stage="await_decision",
        qc_agreement=agreement,
    )
    log_event(
        db,
        "qc.submitted",
        job_id=job.id,
        payload={
            "round_no": round_no,
            "agreement": agreement,
            "changed": total,
            "feedback": body.feedback_text,
        },
    )
    db.commit()
    return {
        "ignored": False,
        "message": f"QC 已提交：更新 {total} 条 label",
        "agreement_rate": agreement,
        "reviewed": total,
        "feedback_id": fb.id,
    }
