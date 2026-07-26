"""QC large model agent — prompt design for confidence-threshold scoring."""

from __future__ import annotations

from typing import Any, Callable

from app.services.labeling import get_threshold
from config import QC_MODEL, QC_TEMPERATURE
from llm_client import qc_chat_json


class QCAgent:
    """质检 / Prompt 优化：固定 Grok API。"""

    name = "qc"

    def __init__(
        self,
        label_schema: dict[str, Any],
        policy_rules: str,
        *,
        on_usage: Callable[[int], None] | None = None,
    ) -> None:
        self.label_schema = label_schema or {}
        self.policy_rules = policy_rules
        self.on_usage = on_usage
        self.model = QC_MODEL

    def _mode_text(self) -> str:
        th = get_threshold(self.label_schema)
        return (
            "Labeling mode: confidence-threshold (NO multi-class label taxonomy).\n"
            "The annotator model must output a confidence score in [0,1] indicating "
            "how strongly the text meets the policy requirements.\n"
            f"Platform rule: confidence >= {th} → label 1; confidence < {th} → label 0.\n"
            "Do not ask the annotator to choose among many free-form category names."
        )

    def design_initial_prompt(self, seed_prompt: str | None = None) -> dict[str, Any]:
        seed = seed_prompt or ""
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert prompt engineer for content-moderation "
                    "confidence scoring (not multi-class labeling).\n"
                    "Design a high-quality scoring prompt for a smaller model.\n"
                    "Return JSON: {"
                    '"prompt_text":"...","change_reason":"中文修改说明",'
                    '"notes":"..."}\n'
                    "change_reason must be short Chinese describing this design."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"{self._mode_text()}\n\n"
                    f"Policy rules:\n{self.policy_rules or '(none)'}\n\n"
                    f"Seed prompt (optional):\n{seed or '(none)'}\n\n"
                    "Requirements:\n"
                    "- Role + policy + CoT guidance for borderline cases\n"
                    "- Require JSON: confidence (0-1), reasoning\n"
                    "- Explain what high vs low confidence means relative to policy\n"
                    "- Do NOT introduce multi-class label names or label descriptions\n"
                    "Return JSON only. change_reason in Chinese."
                ),
            },
        ]
        data = qc_chat_json(
            messages, temperature=QC_TEMPERATURE, on_usage=self.on_usage
        )
        prompt_text = str(data.get("prompt_text") or data.get("prompt") or "").strip()
        if not prompt_text:
            prompt_text = self._fallback_prompt()
        change_reason = str(data.get("change_reason") or "").strip() or (
            "质检大模型根据细则生成初始标注 Prompt"
        )
        return {
            "prompt_text": prompt_text,
            "change_reason": change_reason,
            "notes": data.get("notes"),
            "model": self.model,
        }

    def improve_from_badcases(
        self,
        current_prompt: str,
        badcases: list[dict[str, Any]],
        feedback: str = "",
        metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        bc_text = "\n".join(
            f"- text={b.get('text','')[:180]!r} gold={b.get('gold')} pred={b.get('pred')}"
            for b in badcases[:30]
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "You optimize confidence-scoring prompts for content moderation. "
                    "Analyze badcases and human feedback, then improve the scoring prompt.\n"
                    "Return JSON ONLY with keys: problem_categories, changes, prompt_text, "
                    "change_reason, suggestion_summary.\n"
                    "change_reason MUST be a short Chinese summary of why you changed the prompt "
                    "(1-2 sentences), e.g. 补充刷单话术边界 / 强化冒充公检法识别。\n"
                    "prompt_text is the full improved prompt."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"{self._mode_text()}\n\n"
                    f"Policy rules:\n{self.policy_rules or '(none)'}\n\n"
                    f"Current prompt:\n{current_prompt}\n\n"
                    f"Metrics: {metrics or {}}\n\n"
                    f"Badcases:\n{bc_text or '(none)'}\n\n"
                    f"Human feedback:\n{feedback or '(none)'}\n\n"
                    "Keep confidence-threshold mode; do not introduce multi-class labels. "
                    "Return JSON only. change_reason in Chinese."
                ),
            },
        ]
        data = qc_chat_json(
            messages, temperature=QC_TEMPERATURE, on_usage=self.on_usage
        )
        prompt_text = str(data.get("prompt_text") or data.get("prompt") or "").strip()
        if not prompt_text:
            prompt_text = current_prompt
        change_reason = str(
            data.get("change_reason")
            or data.get("suggestion_summary")
            or data.get("reason")
            or ""
        ).strip()
        if not change_reason:
            change_reason = "质检大模型根据 badcase / 反馈优化提示词"
        summary = str(
            data.get("suggestion_summary") or change_reason or ""
        ).strip()
        return {
            "prompt_text": prompt_text,
            "change_reason": change_reason,
            "improvement_suggestion": {
                "problem_categories": data.get("problem_categories") or [],
                "changes": data.get("changes") or [],
                "suggestion_summary": summary,
                "change_reason": change_reason,
                "source": "qc_llm",
            },
            "model": self.model,
        }

    def _fallback_prompt(self) -> str:
        th = get_threshold(self.label_schema)
        return (
            "Score whether the text meets the content risk-control policy.\n"
            "Think step-by-step about the policy rules, then output JSON only:\n"
            '{"confidence":0.0-1.0,"reasoning":"..."}\n'
            f"Higher confidence = stronger match to policy (threshold={th}).\n"
            f"Rules:\n{self.policy_rules}"
        )
