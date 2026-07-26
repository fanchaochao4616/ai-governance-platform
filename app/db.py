"""SQLAlchemy engine and session."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from config import DB_PATH, ensure_data_dirs

ensure_data_dirs()

DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _connection_record) -> None:  # type: ignore[no-untyped-def]
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401
    from sqlalchemy import inspect, text

    Base.metadata.create_all(bind=engine)

    # Lightweight SQLite migrations
    try:
        insp = inspect(engine)
        if "prompt_templates" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("prompt_templates")}
            with engine.begin() as conn:
                if "description" not in cols:
                    conn.execute(
                        text(
                            "ALTER TABLE prompt_templates "
                            "ADD COLUMN description TEXT"
                        )
                    )
                if "current_version" not in cols:
                    conn.execute(
                        text(
                            "ALTER TABLE prompt_templates "
                            "ADD COLUMN current_version INTEGER DEFAULT 1"
                        )
                    )
                if "updated_at" not in cols:
                    conn.execute(
                        text(
                            "ALTER TABLE prompt_templates "
                            "ADD COLUMN updated_at DATETIME"
                        )
                    )
        if "qc_samples" in insp.get_table_names():
            qcols = {c["name"] for c in insp.get_columns("qc_samples")}
            if "reasoning" not in qcols:
                with engine.begin() as conn:
                    conn.execute(
                        text("ALTER TABLE qc_samples ADD COLUMN reasoning TEXT")
                    )
        # Job 统一任务类型（数据标注 / Prompt / 清洗 / 检索 / 生成）
        if "jobs" in insp.get_table_names():
            jcols = {c["name"] for c in insp.get_columns("jobs")}
            if "job_type" not in jcols:
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            "ALTER TABLE jobs ADD COLUMN job_type "
                            "VARCHAR(64) DEFAULT 'annotation'"
                        )
                    )
                    conn.execute(
                        text(
                            "UPDATE jobs SET job_type = 'annotation' "
                            "WHERE job_type IS NULL OR job_type = ''"
                        )
                    )
        # 数据集管理：元数据列迁移（样本在文件包，不靠 SQL 行）
        if "managed_datasets" in insp.get_table_names():
            mdcols = {c["name"] for c in insp.get_columns("managed_datasets")}
            alters = []
            if "id_column" not in mdcols:
                alters.append(
                    "ALTER TABLE managed_datasets ADD COLUMN id_column VARCHAR(128)"
                )
            if "storage_backend" not in mdcols:
                alters.append(
                    "ALTER TABLE managed_datasets "
                    "ADD COLUMN storage_backend VARCHAR(32) DEFAULT 'files'"
                )
            if "root_path" not in mdcols:
                alters.append(
                    "ALTER TABLE managed_datasets ADD COLUMN root_path VARCHAR(1024)"
                )
            if "raw_archive_path" not in mdcols:
                alters.append(
                    "ALTER TABLE managed_datasets "
                    "ADD COLUMN raw_archive_path VARCHAR(1024)"
                )
            if "source_id_column" not in mdcols:
                alters.append(
                    "ALTER TABLE managed_datasets "
                    "ADD COLUMN source_id_column VARCHAR(128)"
                )
            if "source_text_column" not in mdcols:
                alters.append(
                    "ALTER TABLE managed_datasets "
                    "ADD COLUMN source_text_column VARCHAR(128)"
                )
            if "vector_ready" not in mdcols:
                alters.append(
                    "ALTER TABLE managed_datasets "
                    "ADD COLUMN vector_ready BOOLEAN DEFAULT 0"
                )
            if "vector_model" not in mdcols:
                alters.append(
                    "ALTER TABLE managed_datasets "
                    "ADD COLUMN vector_model VARCHAR(128)"
                )
            if "vector_dim" not in mdcols:
                alters.append(
                    "ALTER TABLE managed_datasets ADD COLUMN vector_dim INTEGER"
                )
            if "vector_count" not in mdcols:
                alters.append(
                    "ALTER TABLE managed_datasets "
                    "ADD COLUMN vector_count INTEGER DEFAULT 0"
                )
            if alters:
                with engine.begin() as conn:
                    for sql in alters:
                        conn.execute(text(sql))
        if "managed_dataset_rows" in insp.get_table_names():
            rcols = {c["name"] for c in insp.get_columns("managed_dataset_rows")}
            with engine.begin() as conn:
                if "content_hash" not in rcols:
                    conn.execute(
                        text(
                            "ALTER TABLE managed_dataset_rows "
                            "ADD COLUMN content_hash VARCHAR(64)"
                        )
                    )
                if "embedding" not in rcols:
                    conn.execute(
                        text("ALTER TABLE managed_dataset_rows ADD COLUMN embedding JSON")
                    )
                if "embedding_model" not in rcols:
                    conn.execute(
                        text(
                            "ALTER TABLE managed_dataset_rows "
                            "ADD COLUMN embedding_model VARCHAR(128)"
                        )
                    )
                if "extra" not in rcols:
                    conn.execute(
                        text("ALTER TABLE managed_dataset_rows ADD COLUMN extra JSON")
                    )
        from app.services.auth_service import ensure_default_admin
        from app.services.dataset_manage_service import migrate_all_file_datasets_to_sql
        from app.services.template_service import ensure_legacy_versions

        db = SessionLocal()
        try:
            ensure_legacy_versions(db)
            ensure_default_admin(db)
            # 旧版磁盘服务文件 → SQL 样本行 + raw 归档
            migrate_all_file_datasets_to_sql(db)
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        # non-fatal on fresh or locked DB
        pass
