"""Job status constants and allowed transitions."""

from __future__ import annotations

from enum import Enum


class JobStatus(str, Enum):
    CREATED = "CREATED"
    GOLD_OPTIMIZING = "GOLD_OPTIMIZING"
    GOLD_FAILED = "GOLD_FAILED"
    GOLD_READY = "GOLD_READY"
    ROUND_LABELING = "ROUND_LABELING"
    AWAIT_DECISION_THRESHOLD = "AWAIT_DECISION_THRESHOLD"  # 全量标注后设判定阈值
    AWAIT_CONFIDENCE_BINS = "AWAIT_CONFIDENCE_BINS"
    AWAIT_QC = "AWAIT_QC"
    AWAIT_DECISION = "AWAIT_DECISION"
    PROMPT_IMPROVING = "PROMPT_IMPROVING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    CANCELLED = "CANCELLED"
    # 中止：人工暂停当前进度，可恢复（重新标注 / 再开跑），不是永久终止
    ABORTED = "ABORTED"


class RoundStatus(str, Enum):
    LABELING = "LABELING"
    AWAIT_THRESHOLD = "AWAIT_THRESHOLD"
    AWAIT_BINS = "AWAIT_BINS"
    AWAIT_QC = "AWAIT_QC"
    AWAIT_DECISION = "AWAIT_DECISION"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# Allowed next statuses from each state (soft guide; services enforce business rules)
TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.CREATED: {
        JobStatus.GOLD_OPTIMIZING,
        JobStatus.CANCELLED,
        JobStatus.FAILED,
    },
    JobStatus.GOLD_OPTIMIZING: {
        JobStatus.GOLD_READY,
        JobStatus.GOLD_FAILED,
        JobStatus.BUDGET_EXCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.ABORTED,  # 中止
    },
    JobStatus.GOLD_FAILED: {
        JobStatus.GOLD_OPTIMIZING,  # 点「重新标注」开下一轮
        JobStatus.PROMPT_IMPROVING,
        JobStatus.CANCELLED,
        JobStatus.ABORTED,
    },
    JobStatus.GOLD_READY: {
        JobStatus.ROUND_LABELING,
        JobStatus.GOLD_OPTIMIZING,  # 可再次重开 loop
        JobStatus.CANCELLED,
        JobStatus.ABORTED,
    },
    JobStatus.ROUND_LABELING: {
        JobStatus.AWAIT_DECISION_THRESHOLD,
        JobStatus.BUDGET_EXCEEDED,
        JobStatus.FAILED,
        JobStatus.ABORTED,
    },
    JobStatus.AWAIT_DECISION_THRESHOLD: {
        JobStatus.AWAIT_CONFIDENCE_BINS,
        JobStatus.GOLD_OPTIMIZING,  # 允许中途重新标注开新 loop
        JobStatus.CANCELLED,
        JobStatus.ABORTED,
    },
    JobStatus.AWAIT_CONFIDENCE_BINS: {
        JobStatus.AWAIT_QC,
        JobStatus.GOLD_OPTIMIZING,
        JobStatus.CANCELLED,
        JobStatus.ABORTED,
    },
    JobStatus.AWAIT_QC: {
        JobStatus.AWAIT_DECISION,
        JobStatus.GOLD_OPTIMIZING,
        JobStatus.CANCELLED,
        JobStatus.ABORTED,
    },
    JobStatus.AWAIT_DECISION: {
        JobStatus.PROMPT_IMPROVING,
        JobStatus.GOLD_OPTIMIZING,
        JobStatus.COMPLETED,
        JobStatus.CANCELLED,
        JobStatus.ABORTED,
    },
    JobStatus.PROMPT_IMPROVING: {
        JobStatus.GOLD_OPTIMIZING,
        JobStatus.GOLD_FAILED,
        JobStatus.GOLD_READY,
        JobStatus.ROUND_LABELING,
        JobStatus.BUDGET_EXCEEDED,
        JobStatus.FAILED,
        JobStatus.ABORTED,
    },
    JobStatus.COMPLETED: {
        JobStatus.GOLD_OPTIMIZING,  # 允许从「已完成」再开下一轮 loop
        JobStatus.CANCELLED,
    },
    JobStatus.FAILED: {JobStatus.CANCELLED, JobStatus.GOLD_OPTIMIZING},
    JobStatus.BUDGET_EXCEEDED: {JobStatus.CANCELLED, JobStatus.GOLD_OPTIMIZING},
    JobStatus.CANCELLED: set(),  # 终止：不可恢复
    # 中止：可点「重新标注」/再开跑恢复
    JobStatus.ABORTED: {
        JobStatus.GOLD_OPTIMIZING,
        JobStatus.PROMPT_IMPROVING,
        JobStatus.CANCELLED,
    },
}


def can_transition(current: str | JobStatus, target: str | JobStatus) -> bool:
    cur = JobStatus(current) if not isinstance(current, JobStatus) else current
    tgt = JobStatus(target) if not isinstance(target, JobStatus) else target
    return tgt in TRANSITIONS.get(cur, set())
