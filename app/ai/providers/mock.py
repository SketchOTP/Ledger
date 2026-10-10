"""Mock AI provider for tests and offline use."""

from typing import Any

from app.ai.providers.base import AIProvider


class MockAIProvider(AIProvider):
    def health_check(self) -> dict[str, Any]:
        return {"ok": True, "provider": "mock"}

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "executive_summary": "Mock CFO summary — connect LM Studio for live AI.",
            "cash_status": "tight",
            "top_actions": [{"title": "Review bills", "detail": "Ensure required bills covered"}],
            "risk_flags": [],
            "surplus_strategy": ["Build emergency buffer first"],
            "debt_strategy": ["Use avalanche when cashflow safe"],
            "research_needed": ["Mortgage rates", "HYSA rates"],
            "assumptions": ["Mock provider — no live model"],
            "confidence": 0.5,
        }
