"""Smoke: call local Ollama annotator (OpenAI-compatible)."""
from __future__ import annotations

from config import ANNOTATOR_BASE_URL, ANNOTATOR_MODEL
from llm_client import annotator_chat_json, get_annotator_client


def main() -> None:
    print("ANNOTATOR_BASE_URL =", ANNOTATOR_BASE_URL)
    print("ANNOTATOR_MODEL    =", ANNOTATOR_MODEL)
    get_annotator_client()
    data = annotator_chat_json(
        [
            {
                "role": "system",
                "content": "You are a scorer. Reply with ONLY a JSON object.",
            },
            {
                "role": "user",
                "content": (
                    'Return JSON: {"confidence": <float 0-1>, "reasoning": "<short>"}. '
                    "Text: hello world"
                ),
            },
        ],
        temperature=0.2,
    )
    print("OK:", data)


if __name__ == "__main__":
    main()
