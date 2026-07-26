"""LLM clients: QC = cloud OpenAI-compatible (Grok / DeepSeek); Annotator = Ollama or cloud."""

from __future__ import annotations

import json
import re
from typing import Any, Callable

import httpx
from openai import OpenAI

from config import (
    ANNOTATOR_BASE_URL,
    ANNOTATOR_MODEL,
    QC_BASE_URL,
    QC_MODEL,
    annotator_should_trust_env,
    get_annotator_api_key,
    get_qc_api_key,
    qc_should_trust_env,
)

_qc_client: OpenAI | None = None
_annotator_client: OpenAI | None = None


def _make_client(base_url: str, api_key: str, trust_env: bool) -> OpenAI:
    http_client = httpx.Client(trust_env=trust_env, timeout=httpx.Timeout(600.0))
    return OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)


def get_client() -> OpenAI:
    """质检 / Prompt 优化：云端（Grok 或 DeepSeek 等），禁止本机 Ollama。"""
    global _qc_client
    if _qc_client is None:
        _qc_client = _make_client(
            QC_BASE_URL, get_qc_api_key(), qc_should_trust_env()
        )
    return _qc_client


def get_annotator_client() -> OpenAI:
    """标注小模型：可为 Ollama 或云端。"""
    global _annotator_client
    if _annotator_client is None:
        _annotator_client = _make_client(
            ANNOTATOR_BASE_URL,
            get_annotator_api_key(),
            annotator_should_trust_env(),
        )
    return _annotator_client


def chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.7,
    on_usage: Callable[[int], None] | None = None,
    client: OpenAI | None = None,
) -> str:
    """Simple chat completion helper for agents."""
    c = client or get_client()
    resp = c.chat.completions.create(
        model=model or QC_MODEL,
        messages=messages,
        temperature=temperature,
    )
    if on_usage and getattr(resp, "usage", None):
        total = int(getattr(resp.usage, "total_tokens", 0) or 0)
        if total:
            on_usage(total)
    content = resp.choices[0].message.content
    return content or ""


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", text)
        if match:
            return json.loads(match.group(0))
        raise


def chat_json(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.2,
    max_retries: int = 2,
    on_usage: Callable[[int], None] | None = None,
    client: OpenAI | None = None,
) -> dict[str, Any]:
    """Chat and parse a JSON object; retry on parse failure."""
    last_err: Exception | None = None
    msgs = list(messages)
    for attempt in range(max_retries + 1):
        try:
            raw = chat(
                msgs,
                model=model,
                temperature=temperature,
                on_usage=on_usage,
                client=client,
            )
            data = _extract_json(raw)
            if isinstance(data, dict):
                return data
            raise ValueError(f"Expected JSON object, got {type(data).__name__}")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            msgs = list(messages) + [
                {
                    "role": "user",
                    "content": (
                        "Your previous reply was not valid JSON. "
                        f"Error: {exc}. Reply with ONLY a single JSON object."
                    ),
                }
            ]
    raise RuntimeError(f"chat_json failed after retries: {last_err}")


def qc_chat(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.4,
    on_usage: Callable[[int], None] | None = None,
) -> str:
    return chat(
        messages,
        model=QC_MODEL,
        temperature=temperature,
        on_usage=on_usage,
        client=get_client(),
    )


def annotator_chat(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.2,
    on_usage: Callable[[int], None] | None = None,
) -> str:
    return chat(
        messages,
        model=ANNOTATOR_MODEL,
        temperature=temperature,
        on_usage=on_usage,
        client=get_annotator_client(),
    )


def annotator_chat_json(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.2,
    on_usage: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    return chat_json(
        messages,
        model=ANNOTATOR_MODEL,
        temperature=temperature,
        on_usage=on_usage,
        client=get_annotator_client(),
    )


def qc_chat_json(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.4,
    on_usage: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    return chat_json(
        messages,
        model=QC_MODEL,
        temperature=temperature,
        on_usage=on_usage,
        client=get_client(),
    )
