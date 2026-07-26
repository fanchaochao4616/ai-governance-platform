"""Export annotations and Gold Test to CSV / Excel."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.models import AnnotationRecord, GoldTestItem, Job, Round
from app.state_machine import JobStatus, RoundStatus
from config import EXPORT_DIR, ensure_data_dirs


def _records_frames(
    job: Job, records: list[AnnotationRecord]
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    max_round = 0
    for r in records:
        for rr in r.rounds or []:
            max_round = max(max_round, int(rr.get("round") or 0))

    ann_rows: list[dict[str, Any]] = []
    round_rows: list[dict[str, Any]] = []
    for r in records:
        row: dict[str, Any] = {
            "seq": r.seq,
            "text": r.text,
            "external_id": r.external_id,
            "final_label": r.final_label,
            "current_label": r.current_label,
            "current_confidence": r.current_confidence,
            "conflict": r.conflict,
        }
        by_round = {
            int(x.get("round")): x
            for x in (r.rounds or [])
            if x.get("round") is not None
        }
        for rn in range(1, max_round + 1):
            rr = by_round.get(rn) or {}
            row[f"round{rn}_label"] = rr.get("label")
            row[f"round{rn}_confidence"] = rr.get("confidence")
            row[f"round{rn}_reasoning"] = rr.get("reasoning")
            if rr:
                round_rows.append(
                    {
                        "seq": r.seq,
                        "round": rn,
                        "label": rr.get("label"),
                        "confidence": rr.get("confidence"),
                        "reasoning": rr.get("reasoning"),
                        "prompt_version_id": rr.get("prompt_version_id"),
                        "model": rr.get("model"),
                        "created_at": rr.get("created_at"),
                    }
                )
        ann_rows.append(row)

    meta_rows = [
        {"key": "job_id", "value": job.id},
        {"key": "name", "value": job.name},
        {"key": "status", "value": job.status},
        {"key": "tokens_used", "value": job.tokens_used},
        {"key": "token_budget", "value": job.token_budget},
        {"key": "average_rounds", "value": str(job.average_rounds)},
        {"key": "target_accuracy", "value": job.target_accuracy},
        {"key": "last_gold_metrics", "value": str(job.last_gold_metrics)},
        {"key": "current_round_no", "value": job.current_round_no},
    ]
    return pd.DataFrame(ann_rows), pd.DataFrame(round_rows), pd.DataFrame(meta_rows)  # type: ignore[return-value]


def has_gold_samples(db: Session, job: Job) -> bool:
    """是否存在 Gold Test 数据。"""
    n = (
        db.query(GoldTestItem)
        .filter(GoldTestItem.job_id == job.id)
        .count()
    )
    return int(n or 0) > 0


# 全量标注成功后的业务状态（可下载标注结果）
_FULL_LABEL_DONE_STATUSES = {
    JobStatus.AWAIT_DECISION_THRESHOLD.value,
    JobStatus.AWAIT_CONFIDENCE_BINS.value,
    JobStatus.AWAIT_QC.value,
    JobStatus.AWAIT_DECISION.value,
    JobStatus.PROMPT_IMPROVING.value,
    JobStatus.COMPLETED.value,
}


def has_successful_full_label(db: Session, job: Job) -> bool:
    """
    当前任务是否已成功完成至少一轮全量/子集标注。
    - 状态已进入阈值/分层/QC/决策等人工阶段 → 是
    - 存在已完成标注的 Round（非 LABELING/FAILED 且 labeled_count>0）→ 是
    - progress 显示 labeled >= label_target 且未中途冻结 → 是
    中止于 Gold 或全量未跑完 → 否
    """
    if job.status in _FULL_LABEL_DONE_STATUSES:
        return True

    # 有已完成的标注轮
    done_round = (
        db.query(Round)
        .filter(
            Round.job_id == job.id,
            Round.labeled_count > 0,
            Round.status.notin_(
                [RoundStatus.LABELING.value, RoundStatus.FAILED.value]
            ),
        )
        .first()
    )
    if done_round is not None:
        return True

    prog = job.progress or {}
    labeled = int(prog.get("labeled") or 0)
    target = int(prog.get("label_target") or 0)
    # 中止冻结且未标满 → 不算成功
    if prog.get("full_label_frozen") and (target <= 0 or labeled < target):
        return False
    if target > 0 and labeled >= target:
        return True

    # 兜底：数据集全部已有置信度，且至少有一轮 Round 记录
    total = (
        db.query(AnnotationRecord)
        .filter(AnnotationRecord.job_id == job.id)
        .count()
    )
    if total <= 0:
        return False
    scored = (
        db.query(AnnotationRecord)
        .filter(
            AnnotationRecord.job_id == job.id,
            AnnotationRecord.current_confidence.isnot(None),
        )
        .count()
    )
    has_round = (
        db.query(Round).filter(Round.job_id == job.id).count() > 0
    )
    return bool(has_round and scored >= total)


def _gold_eval_lookup(job: Job) -> dict[str, dict[str, Any]]:
    """
    从最近一次 Gold 准确率评测 (job.last_gold_metrics.details) 建索引。
    key: gold item id 或 text，value 含 confidence / pred_label 等。
    未做过 Gold 评测时返回空 dict。
    """
    m = job.last_gold_metrics or {}
    details = list(m.get("details") or [])
    by_id: dict[str, dict[str, Any]] = {}
    by_text: dict[str, dict[str, Any]] = {}
    for d in details:
        if not isinstance(d, dict):
            continue
        conf = d.get("confidence")
        try:
            conf_f = float(conf) if conf is not None else None
        except (TypeError, ValueError):
            conf_f = None
        payload = {
            "pred_label": d.get("pred_label"),
            "confidence": conf_f,
            "correct": d.get("correct"),
            "reasoning": d.get("reasoning"),
        }
        gid = d.get("id")
        if gid is not None:
            by_id[str(gid)] = payload
        text = d.get("text")
        if text is not None and str(text) not in by_text:
            by_text[str(text)] = payload
    return {"by_id": by_id, "by_text": by_text}


def _gold_frame(db: Session, job: Job) -> pd.DataFrame:
    """
    Gold Test 集：text + gold_label + source 等。
    若做过 Gold 准确率评测，则附带标注模型 pred_label / confidence；
    未评测时 confidence、pred_label 等为空。
    """
    items = (
        db.query(GoldTestItem)
        .filter(GoldTestItem.job_id == job.id)
        .order_by(GoldTestItem.id.asc())
        .all()
    )
    lookup = _gold_eval_lookup(job)
    by_id = lookup.get("by_id") or {}
    by_text = lookup.get("by_text") or {}

    rows: list[dict[str, Any]] = []
    for i, g in enumerate(items, start=1):
        hit = by_id.get(str(g.id)) or by_text.get(str(g.text or "")) or {}
        conf = hit.get("confidence")
        rows.append(
            {
                "job_id": job.id,
                "row": i,
                "id": g.id,
                "text": g.text,
                "gold_label": g.gold_label,
                # 有 Gold 评测则写入模型预测与置信度，否则空
                "pred_label": hit.get("pred_label"),
                "confidence": conf if conf is not None else None,
                "correct": hit.get("correct"),
                "model_reasoning": hit.get("reasoning"),
                "source": g.source,
                "round_id": g.round_id,
                "seq": g.seq,
                "external_id": g.external_id,
                "created_at": g.created_at.isoformat() if g.created_at else None,
            }
        )
    if not rows:
        return pd.DataFrame(
            columns=[
                "job_id",
                "row",
                "id",
                "text",
                "gold_label",
                "pred_label",
                "confidence",
                "correct",
                "model_reasoning",
                "source",
                "round_id",
                "seq",
                "external_id",
                "created_at",
            ]
        )
    return pd.DataFrame(rows)


def export_job(
    db: Session,
    job: Job,
    *,
    fmt: str = "xlsx",
) -> Path:
    ensure_data_dirs()
    if not has_successful_full_label(db, job):
        raise ValueError("未进行全量标注")
    records = (
        db.query(AnnotationRecord)
        .filter(AnnotationRecord.job_id == job.id)
        .order_by(AnnotationRecord.seq.asc())
        .all()
    )
    ann_df, rounds_df, meta_df = _records_frames(job, records)  # type: ignore[misc]
    gold_df = _gold_frame(db, job)
    fmt = fmt.lower().strip()
    if fmt in {"xlsx", "excel"}:
        out = EXPORT_DIR / f"job_{job.id}_export.xlsx"
        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            ann_df.to_excel(writer, sheet_name="annotations", index=False)
            rounds_df.to_excel(writer, sheet_name="rounds", index=False)
            gold_df.to_excel(writer, sheet_name="gold_test", index=False)
            meta_df.to_excel(writer, sheet_name="meta", index=False)
        return out

    if fmt == "csv":
        # zip: annotations + rounds + gold_test + meta
        out = EXPORT_DIR / f"job_{job.id}_export_csv.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "annotations.csv",
                ann_df.to_csv(index=False).encode("utf-8-sig"),
            )
            zf.writestr(
                "rounds.csv",
                rounds_df.to_csv(index=False).encode("utf-8-sig"),
            )
            zf.writestr(
                "gold_test.csv",
                gold_df.to_csv(index=False).encode("utf-8-sig"),
            )
            zf.writestr(
                "meta.csv",
                meta_df.to_csv(index=False).encode("utf-8-sig"),
            )
        return out

    raise ValueError("format must be csv or xlsx")


def export_gold_job(
    db: Session,
    job: Job,
    *,
    fmt: str = "xlsx",
) -> Path:
    """仅导出 Gold Test（Excel 或 CSV）。无数据时抛出 ValueError。"""
    ensure_data_dirs()
    if not has_gold_samples(db, job):
        raise ValueError("没有 Gold Test 内容")
    gold_df = _gold_frame(db, job)
    fmt = fmt.lower().strip()
    if fmt in {"xlsx", "excel"}:
        out = EXPORT_DIR / f"job_{job.id}_gold.xlsx"
        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            gold_df.to_excel(writer, sheet_name="gold_test", index=False)
        return out
    if fmt == "csv":
        out = EXPORT_DIR / f"job_{job.id}_gold.csv"
        gold_df.to_csv(out, index=False, encoding="utf-8-sig")
        return out
    raise ValueError("format must be csv or xlsx")


def export_bytes(
    db: Session,
    job: Job,
    *,
    fmt: str = "xlsx",
) -> tuple[bytes, str, str, dict[str, Any]]:
    """
    全量导出。未成功全量标注时抛出 ValueError「未进行全量标注」。
    返回 (content, media_type, filename, info)。
    """
    path = export_job(db, job, fmt=fmt)
    data = path.read_bytes()
    info = {
        "has_gold": has_gold_samples(db, job),
        "full_label_done": True,
    }
    if fmt.lower() in {"xlsx", "excel"}:
        return (
            data,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            path.name,
            info,
        )
    return data, "application/zip", path.name, info


def export_gold_bytes(
    db: Session,
    job: Job,
    *,
    fmt: str = "xlsx",
) -> tuple[bytes, str, str]:
    """Return (content, media_type, filename) for Gold Test-only export."""
    path = export_gold_job(db, job, fmt=fmt)
    data = path.read_bytes()
    if fmt.lower() in {"xlsx", "excel"}:
        return (
            data,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            path.name,
        )
    return data, "text/csv; charset=utf-8", path.name
