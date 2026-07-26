"""SQLAlchemy models for the annotation platform."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """内部账号（SQLite 持久化）。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(64), default="user")  # admin | user
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserSession(Base):
    """登录会话 token（存在 SQL 中，支持服务端注销）。"""

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    # 任务类型：annotation | prompt_debug | data_clean | data_search | data_generate | llm_mine
    # 统一进 Job 列表，便于跨模块恢复
    job_type: Mapped[str] = mapped_column(
        String(64), default="annotation", index=True
    )
    status: Mapped[str] = mapped_column(String(64), default="CREATED", index=True)
    label_schema: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    policy_rules: Mapped[str] = mapped_column(Text, default="")
    target_accuracy: Mapped[float] = mapped_column(Float, default=1.0)
    max_gold_iterations: Mapped[int] = mapped_column(Integer, default=5)
    # 0 = unlimited (user no longer configures budget in UI)
    token_budget: Mapped[int] = mapped_column(Integer, default=0)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    confidence_bins: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    average_rounds: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    template_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_round_no: Mapped[int] = mapped_column(Integer, default=0)
    gold_iteration: Mapped[int] = mapped_column(Integer, default=0)
    last_gold_metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    progress: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    annotations: Mapped[list[AnnotationRecord]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    gold_items: Mapped[list[GoldTestItem]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    rounds: Mapped[list[Round]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    prompt_versions: Mapped[list[PromptVersion]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class AnnotationRecord(Base):
    __tablename__ = "annotation_records"
    __table_args__ = (UniqueConstraint("job_id", "seq", name="uq_job_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # List of {round, label, confidence, reasoning, prompt_version_id, model, created_at}
    rounds: Mapped[list] = mapped_column(JSON, default=list)
    final_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    current_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    current_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    conflict: Mapped[bool] = mapped_column(Boolean, default=False)

    job: Mapped[Job] = relationship(back_populates="annotations")


class GoldTestItem(Base):
    __tablename__ = "gold_test_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    gold_label: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="initial")  # initial|qc_correction
    round_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    job: Mapped[Job] = relationship(back_populates="gold_items")


class Round(Base):
    __tablename__ = "rounds"
    __table_args__ = (UniqueConstraint("job_id", "round_no", name="uq_job_round"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    round_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="LABELING")
    prompt_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence_ranges: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    target_ranges_for_labeling: Mapped[list | None] = mapped_column(JSON, nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    human_decision: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    labeled_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    job: Mapped[Job] = relationship(back_populates="rounds")
    qc_samples: Mapped[list[QCSample]] = relationship(
        back_populates="round", cascade="all, delete-orphan"
    )


class QCSample(Base):
    __tablename__ = "qc_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    pred_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 模型判定依据（来自 Annotator 的 reasoning）
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    bin_name: Mapped[str] = mapped_column(String(32), default="Medium")
    human_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    corrected: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)

    round: Mapped[Round] = relationship(back_populates="qc_samples")


class QCFeedback(Base):
    __tablename__ = "qc_feedbacks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), index=True)
    feedback_text: Mapped[str] = mapped_column(Text, default="")
    agreement_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (UniqueConstraint("job_id", "version", name="uq_job_prompt_ver"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    parent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    qc_feedback_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    improvement_suggestion: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    job: Mapped[Job] = relationship(back_populates="prompt_versions")


class PromptTemplate(Base):
    """Prompt 模板库条目（逻辑实体；正文在版本表中做版本控制）。"""

    __tablename__ = "prompt_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[str] = mapped_column(String(128), default="general")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 缓存当前激活版本正文，便于列表与 Job 启动快速读取
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    tags: Mapped[list | None] = mapped_column(JSON, default=list)
    source_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_prompt_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    versions: Mapped[list[PromptTemplateVersion]] = relationship(
        back_populates="template", cascade="all, delete-orphan"
    )


class PromptTemplateVersion(Base):
    """模板正文版本：只增不改；激活某版本 = 回滚/选用。"""

    __tablename__ = "prompt_template_versions"
    __table_args__ = (
        UniqueConstraint("template_id", "version", name="uq_template_ver"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("prompt_templates.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    source_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_prompt_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    template: Mapped[PromptTemplate] = relationship(back_populates="versions")


class EventLog(Base):
    __tablename__ = "event_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    event: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ManagedDataset(Base):
    """数据集元数据（SQL 只存目录信息，样本在训练文件包中）。

    在线主路径 = 文件系统训练包::

        data/datasets/{id}/data.jsonl   # 训练首选
        data/datasets/{id}/data.csv
        data/datasets/{id}/manifest.json
        data/datasets/{id}/vectors/     # 向量索引
        data/datasets/{id}/media/       # 图片等二进制
        data/datasets/{id}/raw/         # 原始上传

    SQL 不作为训练读数据路径；便于后续换 PG 只迁元数据。
    """

    __tablename__ = "managed_datasets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # text | image | audio | video
    modality: Mapped[str] = mapped_column(String(32), default="text", index=True)
    # 来源上传格式
    file_format: Mapped[str] = mapped_column(String(32), default="csv")
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # files = 训练包（JSONL/CSV）；兼容旧值 sql
    storage_backend: Mapped[str] = mapped_column(String(32), default="files", index=True)
    # 包根目录相对 DATA_DIR 或绝对路径
    root_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    raw_archive_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # 旧版兼容
    storage_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source_id_column: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_text_column: Mapped[str | None] = mapped_column(String(128), nullable=True)
    id_column: Mapped[str | None] = mapped_column(String(128), nullable=True)
    text_column: Mapped[str | None] = mapped_column(String(128), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    column_count: Mapped[int] = mapped_column(Integer, default=0)
    columns: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 向量索引状态
    vector_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    vector_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    vector_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vector_count: Mapped[int] = mapped_column(Integer, default=0)
    # ready | empty | error | indexing
    status: Mapped[str] = mapped_column(String(32), default="empty", index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


class ManagedDatasetRow(Base):
    """旧版 SQL 样本行（兼容迁移；新数据不再写入）。"""

    __tablename__ = "managed_dataset_rows"
    __table_args__ = (
        UniqueConstraint("dataset_id", "seq", name="uq_dataset_row_seq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("managed_datasets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    external_id: Mapped[str | None] = mapped_column(
        String(512), nullable=True, index=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)
