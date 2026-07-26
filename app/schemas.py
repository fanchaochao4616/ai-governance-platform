"""Pydantic API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# 平台统一 Job 类型（均进入 Job 列表，便于恢复）
JOB_TYPES = (
    "annotation",  # 数据标注
    "prompt_debug",  # Prompt 调试
    "data_clean",  # 数据清洗
    "data_search",  # 数据库检索
    "data_generate",  # 数据生成
    "llm_mine",  # 大模型挖掘
)


class JobCreate(BaseModel):
    """Create job. 各任务类型共用；annotation 需细则，其它类型可简化创建。"""

    name: str
    job_type: str = Field(
        default="annotation",
        description="annotation | prompt_debug | data_clean | data_search | data_generate | llm_mine",
    )
    policy_rules: str = Field(
        default="",
        description=(
            "风控细则与判定说明（数据标注必填）：既是政策依据，也是初始 Prompt 种子"
        ),
    )
    # 判定阈值在全量标注后设置，创建 Job 时不填
    target_accuracy: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Gold 测试集上目标正确率；达到后停止 Prompt 优化",
    )
    max_gold_iterations: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Gold 上最多迭代改 Prompt 的次数",
    )
    # 不再要求用户配置；0 表示不限制（见 budget.py）
    token_budget: int | None = Field(default=None, description="可选；默认不限制")
    template_id: int | None = None
    # 已废弃：请写入 policy_rules。若仍传入则优先于 policy_rules 作为 Prompt v1 文案
    initial_prompt: str | None = Field(
        default=None,
        description="Deprecated: merged into policy_rules",
    )
    # 提示词调试首次保存：写入 Prompt v1 的 change_reason
    seed_change_reason: str | None = Field(
        default=None,
        description="Optional change_reason for Prompt v1 (prompt_debug first save)",
    )


class JobOut(BaseModel):
    id: int
    name: str
    job_type: str = "annotation"
    status: str
    label_schema: dict[str, Any]
    policy_rules: str
    decision_threshold: float | None = None
    threshold_set: bool = False
    target_accuracy: float
    max_gold_iterations: int
    token_budget: int
    tokens_used: int
    confidence_bins: dict[str, Any] | None = None
    average_rounds: dict[str, Any] | None = None
    current_round_no: int
    gold_iteration: int
    last_gold_metrics: dict[str, Any] | None = None
    progress: dict[str, Any] | None = None
    error_message: str | None = None
    annotation_count: int = 0
    gold_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class JobNameUpdate(BaseModel):
    """更新任务显示名称。"""

    name: str = Field(..., min_length=1, max_length=256, description="任务名称")


class JobGoldParamsUpdate(BaseModel):
    """更新 Gold 优化参数（目标准确率 / 最大迭代次数）。至少传一项。"""

    target_accuracy: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Gold 测试集目标准确率 0~1",
    )
    max_gold_iterations: int | None = Field(
        default=None,
        ge=1,
        le=50,
        description="Gold 最大迭代次数 1~50",
    )


class DecisionThresholdRequest(BaseModel):
    """全量标注完成后设置：confidence >= threshold → label 1."""

    threshold: float = Field(ge=0.0, le=1.0)
    round_no: int | None = None


class ConfidenceBin(BaseModel):
    name: str  # High | Medium | Low
    min: float = Field(ge=0.0, le=1.0)
    max: float = Field(ge=0.0, le=1.0)


class ConfidenceBinsRequest(BaseModel):
    bins: list[ConfidenceBin]
    qc_per_bin: int | None = None


class QCReviewItem(BaseModel):
    seq: int
    human_label: str
    corrected: bool = False


class QCSubmitRequest(BaseModel):
    reviews: list[QCReviewItem]
    feedback_text: str = ""


class DecisionRequest(BaseModel):
    continue_next: bool
    feedback: str = ""
    next_confidence_ranges: list[str] = Field(
        default_factory=list,
        description="Bin names to re-label next round, e.g. ['Low','Medium']",
    )
    # 若提供，直接作为新 Prompt 版本（人工编辑），跳过质检大模型自动改写
    prompt_text: str | None = None
    change_reason: str | None = None


class PromptVersionCreateBody(BaseModel):
    prompt_text: str
    change_reason: str | None = "manual edit"


class FinalizeRequest(BaseModel):
    from_round: int = 1
    to_round: int | None = None
    selected_rounds: list[int] | None = None
    # 默认 False：分层抽 QC 时也会调用，不应把任务标成「已完成」导致无法重新标注
    mark_completed: bool = False


class JobBulkDeleteRequest(BaseModel):
    """批量永久删除 Job。"""

    ids: list[int] = Field(default_factory=list, min_length=1)


class PromptVersionOut(BaseModel):
    id: int
    job_id: int
    version: int
    prompt_text: str
    parent_version: int | None = None
    metrics: dict[str, Any] | None = None
    change_reason: str | None = None
    tokens_used: int = 0
    is_active: bool = False
    improvement_suggestion: dict[str, Any] | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class TemplateCreate(BaseModel):
    name: str
    category: str = "general"
    description: str | None = None
    prompt_text: str
    change_reason: str | None = "initial create"
    score: float = 0.0
    tags: list[str] = Field(default_factory=list)
    source_job_id: int | None = None
    source_prompt_version_id: int | None = None


class TemplateMetaUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    description: str | None = None
    score: float | None = None
    tags: list[str] | None = None


class TemplateVersionCreate(BaseModel):
    prompt_text: str
    change_reason: str | None = None
    source_job_id: int | None = None
    source_prompt_version_id: int | None = None


class TemplateVersionOut(BaseModel):
    id: int
    template_id: int
    version: int
    prompt_text: str
    change_reason: str | None = None
    parent_version: int | None = None
    is_active: bool = False
    source_job_id: int | None = None
    source_prompt_version_id: int | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class TemplateOut(BaseModel):
    id: int
    name: str
    category: str
    description: str | None = None
    prompt_text: str
    current_version: int = 1
    version_count: int | None = None
    score: float
    tags: list[str] | None = None
    source_job_id: int | None = None
    source_prompt_version_id: int | None = None
    usage_count: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class QCSampleOut(BaseModel):
    id: int
    seq: int
    text: str
    pred_label: str | None
    confidence: float | None
    # 对应导出列 round{N}_reasoning
    reasoning: str | None = None
    reasoning_round: int | None = None
    reasoning_field: str | None = None  # e.g. "round1_reasoning"
    bin_name: str
    human_label: str | None
    corrected: bool
    reviewed: bool

    model_config = {"from_attributes": True}


class RoundOut(BaseModel):
    id: int
    job_id: int
    round_no: int
    status: str
    prompt_version_id: int | None
    confidence_ranges: dict[str, Any] | None
    target_ranges_for_labeling: list[Any] | None
    metrics: dict[str, Any] | None
    human_decision: dict[str, Any] | None
    labeled_count: int

    model_config = {"from_attributes": True}


class AnnotationHistoryOut(BaseModel):
    seq: int
    text: str
    external_id: str | None
    rounds: list[dict[str, Any]]
    final_label: str | None
    current_label: str | None
    current_confidence: float | None
    conflict: bool


class UploadResult(BaseModel):
    count: int
    errors: list[dict[str, Any]] = Field(default_factory=list)
    skipped: int = 0
