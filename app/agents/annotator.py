"""Annotator agent: policy match confidence. Never sees seq.

Full-dataset labeling stores confidence only; label 1/0 is applied after
the user sets decision_threshold. Gold eval may pass force_threshold.
"""

from __future__ import annotations

from typing import Any, Callable

from app.services.labeling import (
    derive_label,
    get_threshold,
    is_threshold_set,
)
from config import (
    ANNOTATOR_MODEL,
    ANNOTATOR_TEMPERATURE,
    DEFAULT_DECISION_THRESHOLD,
)
from llm_client import annotator_chat_json


class AnnotatorAgent:
    name = "annotator"

    def __init__(
        self,
        label_schema: dict[str, Any],
        policy_rules: str,
        prompt_text: str,
        *,
        few_shots: list[dict[str, str]] | None = None,
        on_usage: Callable[[int], None] | None = None,
    ) -> None:
        self.label_schema = label_schema or {}
        self.policy_rules = policy_rules
        self.prompt_text = prompt_text
        self.few_shots = few_shots or []
        self.on_usage = on_usage
        self.model = ANNOTATOR_MODEL

    def _system(self) -> str:
        few = ""
        if self.few_shots:
            parts = []
            for ex in self.few_shots[:8]:
                parts.append(
                    f"Text: {ex.get('text', '')}\nGold: {ex.get('label', '')}"
                )
            few = "\n\nFew-shot examples:\n" + "\n---\n".join(parts)

        policy = (self.policy_rules or "").strip()
        prompt = (self.prompt_text or "").strip()
        if prompt and policy and prompt == policy:
            criteria_block = f"判定说明（细则 / Prompt）:\n{policy}\n"
        else:
            criteria_block = (
                f"风控细则:\n{policy or '(none)'}\n\n"
                f"当前标注 Prompt:\n{prompt or '(none)'}\n"
            )

        return (
            "You are a content-moderation scorer for policy compliance.\n"
            "Judge how strongly the text MEETS / MATCHES the requirements. "
            "Do NOT invent multi-class taxonomies.\n\n"
            "Output ONLY a JSON object with keys:\n"
            "  - confidence: float in [0,1] — higher = stronger match to policy\n"
            "  - reasoning: brief justification\n\n"
            "Do NOT output a final binary label; the platform applies a threshold later.\n\n"
            f"{criteria_block}"
            f"{few}"
        )

    def annotate(
        self,
        text: str,
        *,
        force_threshold: float | None = None,
    ) -> dict[str, Any]:
        """Score text. Caller must NOT pass seq.

        force_threshold: if set (e.g. gold eval), derive label immediately.
        Otherwise label is only derived when job threshold has been set by user.
        """
        messages = [
            {"role": "system", "content": self._system()},
            {
                "role": "user",
                "content": (
                    "Score the following text against the policy rules. "
                    "Reply with JSON only:\n"
                    '{"confidence":0.0,"reasoning":"..."}\n\n'
                    f"Text:\n{text}"
                ),
            },
        ]
        data = annotator_chat_json(
            messages,
            temperature=ANNOTATOR_TEMPERATURE,
            on_usage=self.on_usage,
        )
        try:
            conf = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        reasoning = str(data.get("reasoning", "") or "")

        label: str | None = None
        applied_th: float | None = None
        if force_threshold is not None:
            applied_th = float(force_threshold)
            label = derive_label(conf, self.label_schema, threshold=applied_th)
        elif is_threshold_set(self.label_schema):
            applied_th = get_threshold(self.label_schema)
            if applied_th is not None:
                label = derive_label(conf, self.label_schema, threshold=applied_th)

        return {
            "label": label,
            "confidence": conf,
            "reasoning": reasoning,
            "model": self.model,
            "decision_threshold": applied_th,
        }
