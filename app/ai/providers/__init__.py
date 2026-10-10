"""AI provider factory."""

from app.ai.provider_registry import (
    ProviderConfigurationError,
    enforce_remote_openai_guard,
    normalize_provider_id,
    resolve_adapter,
    resolve_base_url,
)
from app.ai.providers.base import AIProvider
from app.ai.providers.mock import MockAIProvider
from app.ai.providers.openai_compatible import OpenAICompatibleProvider
from app.core.user_ai_settings import get_effective_ai_settings

__all__ = [
    "AIProvider",
    "ProviderConfigurationError",
    "get_provider",
]


def get_provider() -> AIProvider:
    """Instantiate the configured AI provider (no silent mock fallback)."""
    cfg = get_effective_ai_settings()
    provider_id = normalize_provider_id(cfg.ai_provider)
    adapter, spec, _warnings = resolve_adapter(provider_id)
    base_url = resolve_base_url(provider_id, spec, cfg)
    enforce_remote_openai_guard(base_url, cfg)

    if adapter == "mock":
        return MockAIProvider()

    if adapter == "openai_compatible":
        return OpenAICompatibleProvider(
            base_url=base_url or None,
            api_key=cfg.ai_api_key or None,
            model=cfg.ai_model or None,
        )

    if adapter == "cli":
        from app.ai.providers.cli import get_cli_provider

        return get_cli_provider(
            provider_id,
            model=cfg.ai_model or "gpt-6-luna",
            reasoning_effort=cfg.ai_reasoning_effort,
        )

    raise ProviderConfigurationError(f"Unsupported adapter '{adapter}' for provider '{provider_id}'")
