"""Shared config for content-moderation annotation platform."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / ".env")

# Paths
DATA_DIR = Path(os.getenv("DATA_DIR", str(_ROOT / "data")))
UPLOAD_DIR = DATA_DIR / "uploads"
EXPORT_DIR = DATA_DIR / "exports"
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "app.db")))

# xAI 云端（Grok）— 有额度时作质检首选
XAI_BASE_URL = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/")
XAI_MODEL = os.getenv("XAI_MODEL", "grok-4.5")
XAI_QC_MODEL = os.getenv("XAI_QC_MODEL", XAI_MODEL)

# 质检 QC：OpenAI 兼容云端（Grok / DeepSeek 等，禁止本机 Ollama）
# QC_PROVIDER=deepseek | xai | custom
# deepseek: base=https://api.deepseek.com  model=deepseek-chat
_qc_provider = os.getenv("QC_PROVIDER", "xai").strip().lower()
if _qc_provider in {"deepseek", "ds"}:
    _default_qc_base = "https://api.deepseek.com"
    _default_qc_model = "deepseek-chat"
elif _qc_provider in {"xai", "grok"}:
    _default_qc_base = XAI_BASE_URL
    _default_qc_model = XAI_QC_MODEL
else:
    _default_qc_base = XAI_BASE_URL
    _default_qc_model = XAI_QC_MODEL

QC_BASE_URL = os.getenv("QC_BASE_URL", _default_qc_base).rstrip("/")
QC_MODEL = os.getenv("QC_MODEL", os.getenv("XAI_QC_MODEL", _default_qc_model))
# 兼容旧名
XAI_QC_MODEL = QC_MODEL

# Annotator（标注小模型）可单独指向 Ollama
XAI_ANNOTATOR_MODEL = os.getenv(
    "XAI_ANNOTATOR_MODEL",
    os.getenv("ANNOTATOR_MODEL", "grok-4-1-fast"),
)
ANNOTATOR_MODEL = XAI_ANNOTATOR_MODEL
ANNOTATOR_BASE_URL = os.getenv("ANNOTATOR_BASE_URL", XAI_BASE_URL).rstrip("/")
_annotator_key_env = os.getenv("ANNOTATOR_API_KEY", "").strip()
ANNOTATOR_API_KEY = _annotator_key_env
ANNOTATOR_TRUST_ENV = os.getenv("ANNOTATOR_TRUST_ENV", "").strip().lower() in {
    "1",
    "true",
    "yes",
}

# Annotation defaults
DEFAULT_QC_PER_BIN = int(os.getenv("DEFAULT_QC_PER_BIN", "20"))
ANNOTATOR_CONCURRENCY = int(os.getenv("ANNOTATOR_CONCURRENCY", "5"))
ANNOTATOR_TEMPERATURE = float(os.getenv("ANNOTATOR_TEMPERATURE", "0.2"))
QC_TEMPERATURE = float(os.getenv("QC_TEMPERATURE", "0.4"))

# Confidence-threshold labeling (no multi-class taxonomy)
# Model outputs confidence; after user sets threshold:
# confidence >= decision_threshold → POSITIVE_LABEL ("1"), else NEGATIVE_LABEL ("0").
POSITIVE_LABEL = os.getenv("POSITIVE_LABEL", "1")
NEGATIVE_LABEL = os.getenv("NEGATIVE_LABEL", "0")
DEFAULT_DECISION_THRESHOLD = float(os.getenv("DEFAULT_DECISION_THRESHOLD", "0.5"))

# Auth (SQLite users + sessions)
AUTH_SESSION_DAYS = int(os.getenv("AUTH_SESSION_DAYS", "7"))
DEFAULT_ADMIN_USERNAME = os.getenv("DEFAULT_ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.getenv("DEFAULT_ADMIN_PASSWORD", "admin123")
DEFAULT_ADMIN_DISPLAY_NAME = os.getenv("DEFAULT_ADMIN_DISPLAY_NAME", "管理员")


def get_xai_api_key() -> str:
    """xAI / Grok API Key（可选；质检走 DeepSeek 时可不配）。"""
    return os.getenv("XAI_API_KEY", "").strip()


def _is_local_llm_url(url: str) -> bool:
    u = (url or "").lower()
    return any(
        h in u
        for h in (
            "127.0.0.1",
            "localhost",
            "0.0.0.0",
            "[::1]",
        )
    )


def get_qc_api_key() -> str:
    """
    质检云端密钥（禁止本机 Ollama）。
    优先 QC_API_KEY → DEEPSEEK_API_KEY → XAI_API_KEY。
    """
    for env_name in ("QC_API_KEY", "DEEPSEEK_API_KEY", "XAI_API_KEY"):
        v = os.getenv(env_name, "").strip()
        if v:
            if _is_local_llm_url(QC_BASE_URL):
                raise RuntimeError(
                    "质检模型禁止使用本机 Ollama。请设置 QC_BASE_URL 为云端 "
                    "（Grok: https://api.x.ai/v1 或 DeepSeek: https://api.deepseek.com）"
                )
            return v
    raise RuntimeError(
        "Missing QC API key. 设置 QC_API_KEY / DEEPSEEK_API_KEY（DeepSeek）"
        " 或 XAI_API_KEY（Grok）。"
    )


def get_annotator_api_key() -> str:
    """Annotator：显式 ANNOTATOR_API_KEY > 本地 Ollama 占位 > XAI/QC key。"""
    if ANNOTATOR_API_KEY:
        return ANNOTATOR_API_KEY
    if _is_local_llm_url(ANNOTATOR_BASE_URL):
        return "ollama"
    for env_name in ("XAI_API_KEY", "QC_API_KEY", "DEEPSEEK_API_KEY"):
        v = os.getenv(env_name, "").strip()
        if v:
            return v
    raise RuntimeError("Missing API key for annotator cloud endpoint.")


def qc_should_trust_env() -> bool:
    """QC 云端默认走系统代理。"""
    return True


def annotator_should_trust_env() -> bool:
    """本地 URL 默认 False，避免代理 502。"""
    if os.getenv("ANNOTATOR_TRUST_ENV", "").strip():
        return ANNOTATOR_TRUST_ENV
    return not _is_local_llm_url(ANNOTATOR_BASE_URL)


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
