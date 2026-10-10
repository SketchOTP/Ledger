"""OpenAI-compatible provider (LM Studio, local servers)."""

import json
from typing import Any

from openai import OpenAI

from app.ai.providers.base import AIProvider
from app.config import settings


class OpenAICompatibleProvider(AIProvider):
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self.base_url = base_url or settings.ai_base_url
        self.model = model or settings.ai_model
        self.client = OpenAI(
            base_url=self.base_url,
            api_key=api_key or settings.ai_api_key,
            timeout=timeout or settings.ai_timeout_seconds,
        )

    def health_check(self) -> dict[str, Any]:
        try:
            models = self.client.models.list()
            ids = [m.id for m in models.data[:5]] if models.data else []
            return {"ok": True, "models": ids, "base_url": self.base_url}
        except Exception as e:
            return {"ok": False, "error": str(e), "base_url": self.base_url}

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        schema_hint = json.dumps(schema)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt + "\nRespond with valid JSON only."},
                {"role": "user", "content": user_prompt + f"\n\nJSON schema:\n{schema_hint}"},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        return json.loads(content)
