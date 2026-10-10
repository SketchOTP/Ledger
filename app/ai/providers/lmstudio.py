"""LM Studio provider — OpenAI-compatible local default."""

from app.ai.providers.openai_compatible import OpenAICompatibleProvider


class LMStudioProvider(OpenAICompatibleProvider):
    """Default local LM Studio via OpenAI-compatible API."""
