from __future__ import annotations

import json

from openai import OpenAI

from ...core.config import Settings, get_settings


class SiliconFlowClient:
    """Shared OpenAI-compatible client for all three outreach strategies."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        api_key = self.settings.llm_api_key.get_secret_value().strip()
        if not api_key:
            raise RuntimeError("LLM_API_KEY is not configured")
        if not self.settings.llm_model.strip():
            raise RuntimeError("LLM_MODEL is not configured")

        self.client = OpenAI(
            base_url=self.settings.llm_api_base_url,
            api_key=api_key,
            timeout=self.settings.llm_timeout_seconds,
            max_retries=self.settings.llm_max_retries,
        )

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 600,
        model: str | None = None,
        temperature: float = 0.0,
        enable_thinking: bool | None = False,
    ) -> dict[str, object]:
        extra_body = (
            {"enable_thinking": enable_thinking}
            if enable_thinking is not None
            else None
        )
        response = self.client.chat.completions.create(
            model=(model or self.settings.llm_model).strip(),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            extra_body=extra_body,
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("SiliconFlow returned an empty response")
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise RuntimeError("SiliconFlow response must be a JSON object")
        return payload
