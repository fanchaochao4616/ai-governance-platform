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
        from app.services.auth_service import ensure_default_admin
        from app.services.template_service import ensure_legacy_versions

        db = SessionLocal()
        try:
            ensure_legacy_versions(db)
            ensure_default_admin(db)
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        # non-fatal on fresh or locked DB
        pass
