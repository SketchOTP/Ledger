"""AI provider catalog — metadata, resolution, and configuration validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.core.api_key_preview import api_key_preview
from app.core.user_ai_settings import EffectiveAISettings, get_effective_ai_settings

AuthMode = Literal["none", "local", "api_key", "cli_oauth"]
AdapterKind = Literal["mock", "openai_compatible", "cli"]
StatusKind = Literal["configured", "misconfigured", "unavailable"]


class ProviderConfigurationError(ValueError):
    """Raised when AI provider env is invalid (no silent mock fallback)."""


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    auth_mode: AuthMode
    adapter: AdapterKind
    requires_base_url: bool
    requires_api_key: bool
    default_base_url: str | None = None
    supports_tools: bool = True
    supports_structured_output: bool = True
    implemented: bool = True

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "auth_mode": self.auth_mode,
            "adapter": self.adapter,
            "requires_base_url": self.requires_base_url,
            "requires_api_key": self.requires_api_key,
            "default_base_url": self.default_base_url,
            "supports_tools": self.supports_tools,
            "supports_structured_output": self.supports_structured_output,
            "implemented": self.implemented,
        }


PROVIDER_REGISTRY: dict[str, ProviderSpec] = {
    "mock": ProviderSpec(
        id="mock",
        label="Mock (offline)",
        auth_mode="none",
        adapter="mock",
        requires_base_url=False,
        requires_api_key=False,
        supports_tools=False,
    ),
    "lmstudio": ProviderSpec(
        id="lmstudio",
        label="LM Studio",
        auth_mode="local",
        adapter="openai_compatible",
        requires_base_url=True,
        requires_api_key=False,
        default_base_url="http://localhost:1234/v1",
    ),
    "openai_compatible": ProviderSpec(
        id="openai_compatible",
        label="OpenAI-compatible endpoint",
        auth_mode="api_key",
        adapter="openai_compatible",
        requires_base_url=True,
        requires_api_key=False,
    ),
    "openai": ProviderSpec(
        id="openai",
        label="OpenAI",
        auth_mode="api_key",
        adapter="openai_compatible",
        requires_base_url=True,
        requires_api_key=True,
        default_base_url="https://api.openai.com/v1",
    ),
    "groq": ProviderSpec(
        id="groq",
        label="Groq",
        auth_mode="api_key",
        adapter="openai_compatible",
        requires_base_url=True,
        requires_api_key=True,
        default_base_url="https://api.groq.com/openai/v1",
    ),
    "openrouter": ProviderSpec(
        id="openrouter",
        label="OpenRouter",
        auth_mode="api_key",
        adapter="openai_compatible",
        requires_base_url=True,
        requires_api_key=True,
        default_base_url="https://openrouter.ai/api/v1",
    ),
    "routerbot": ProviderSpec(
        id="routerbot",
        label="Routerbot (OpenAI-compatible)",
        auth_mode="api_key",
        adapter="openai_compatible",
        requires_base_url=True,
        requires_api_key=False,
    ),
    "future_cli_cursor": ProviderSpec(
        id="future_cli_cursor",
        label="Cursor CLI (planned)",
        auth_mode="cli_oauth",
        adapter="cli",
        requires_base_url=False,
        requires_api_key=False,
        implemented=False,
    ),
    "future_cli_gemini": ProviderSpec(
        id="future_cli_gemini",
        label="Gemini CLI (planned)",
        auth_mode="cli_oauth",
        adapter="cli",
        requires_base_url=False,
        requires_api_key=False,
        implemented=False,
    ),
    "future_cli_claude": ProviderSpec(
        id="future_cli_claude",
        label="Claude Code CLI (planned)",
        auth_mode="cli_oauth",
        adapter="cli",
        requires_base_url=False,
        requires_api_key=False,
        implemented=False,
    ),
    "future_cli_codex": ProviderSpec(
        id="future_cli_codex",
        label="Codex CLI (ChatGPT sign-in)",
        auth_mode="cli_oauth",
        adapter="cli",
        requires_base_url=False,
        requires_api_key=False,
        implemented=True,
    ),
}

OPENAI_CLOUD_HOST = "api.openai.com"


def normalize_provider_id(provider_id: str | None) -> str:
    return (provider_id or "").strip().lower()


def get_provider_spec(provider_id: str) -> ProviderSpec | None:
    return PROVIDER_REGISTRY.get(normalize_provider_id(provider_id))


def list_provider_specs() -> list[dict[str, Any]]:
    return [spec.to_public_dict() for spec in PROVIDER_REGISTRY.values()]


def resolve_base_url(
    provider_id: str, spec: ProviderSpec | None, cfg: EffectiveAISettings | None = None
) -> str:
    cfg = cfg or get_effective_ai_settings()
    explicit = (cfg.ai_base_url or "").strip()
    if explicit:
        return explicit.rstrip("/")
    if spec and spec.default_base_url:
        return spec.default_base_url.rstrip("/")
    return ""


def is_remote_openai_url(base_url: str) -> bool:
    return OPENAI_CLOUD_HOST in (base_url or "")


def enforce_remote_openai_guard(
    base_url: str, cfg: EffectiveAISettings | None = None
) -> None:
    cfg = cfg or get_effective_ai_settings()
    if is_remote_openai_url(base_url) and not cfg.ai_allow_remote_openai:
        raise ProviderConfigurationError(
            "Remote OpenAI (https://api.openai.com/v1) is blocked. "
            "Set AI_ALLOW_REMOTE_OPENAI=true to enable cloud OpenAI."
        )


def collect_configuration_warnings(
    provider_id: str,
    spec: ProviderSpec | None,
    base_url: str,
    cfg: EffectiveAISettings | None = None,
) -> list[str]:
    cfg = cfg or get_effective_ai_settings()
    warnings: list[str] = []
    if spec is None:
        warnings.append(
            f"Unknown provider id '{provider_id}'; using openai_compatible because AI_BASE_URL is set."
        )
    elif not spec.implemented:
        warnings.append(f"Provider '{provider_id}' is not implemented yet.")
    effective = spec or get_provider_spec("openai_compatible")
    if effective and effective.requires_base_url and not base_url:
        warnings.append("AI_BASE_URL is required for this provider (or set a registry default).")
    if effective and effective.requires_api_key and not (cfg.ai_api_key or "").strip():
        warnings.append("AI_API_KEY is required for this provider.")
    if is_remote_openai_url(base_url) and cfg.ai_allow_remote_openai:
        warnings.append("Remote OpenAI cloud is enabled (AI_ALLOW_REMOTE_OPENAI=true).")
    return warnings


def resolve_adapter(
    provider_id: str | None = None,
) -> tuple[AdapterKind, ProviderSpec | None, list[str]]:
    """Return adapter kind, optional registry spec, and resolution warnings."""
    cfg = get_effective_ai_settings()
    pid = normalize_provider_id(provider_id or cfg.ai_provider)
    spec = get_provider_spec(pid)
    if spec:
        if not spec.implemented:
            raise ProviderConfigurationError(
                f"AI provider '{pid}' is registered but not implemented yet ({spec.label})."
            )
        return spec.adapter, spec, []

    base_url = (cfg.ai_base_url or "").strip()
    if base_url:
        fallback = get_provider_spec("openai_compatible")
        return "openai_compatible", fallback, [
            f"Unknown provider '{pid}'; using openai_compatible because AI_BASE_URL is set."
        ]

    raise ProviderConfigurationError(
        f"Unknown AI provider '{pid}' and AI_BASE_URL is not set. "
        f"Choose one of: {', '.join(sorted(PROVIDER_REGISTRY))}."
    )


def redact_api_key_present(cfg: EffectiveAISettings | None = None) -> bool:
    cfg = cfg or get_effective_ai_settings()
    key = (cfg.ai_api_key or "").strip()
    return bool(key) and key not in ("lm-studio", "not-needed", "none")


def build_ai_settings_payload(health_check: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build GET /api/settings/ai response (never includes raw API key)."""
    cfg = get_effective_ai_settings()
    provider_id = normalize_provider_id(cfg.ai_provider)
    spec = get_provider_spec(provider_id)
    warnings: list[str] = []
    status: StatusKind = "configured"
    auth_mode: AuthMode = spec.auth_mode if spec else "api_key"
    adapter: AdapterKind = spec.adapter if spec else "openai_compatible"
    base_url = ""

    try:
        adapter, spec, resolve_warnings = resolve_adapter(provider_id)
        warnings.extend(resolve_warnings)
        base_url = resolve_base_url(provider_id, spec, cfg)
        warnings.extend(collect_configuration_warnings(provider_id, spec, base_url, cfg))
        enforce_remote_openai_guard(base_url, cfg)
        auth_mode = spec.auth_mode if spec else "api_key"
    except ProviderConfigurationError as exc:
        status = "misconfigured"
        warnings.append(str(exc))
        base_url = resolve_base_url(provider_id, spec, cfg)
        if spec:
            auth_mode = spec.auth_mode
            adapter = spec.adapter

    if health_check is None:
        health_check = {}
    if health_check and not health_check.get("ok") and status == "configured":
        status = "unavailable"

    return {
        "provider": provider_id,
        "provider_label": spec.label if spec else provider_id,
        "adapter": adapter,
        "auth_mode": auth_mode,
        "base_url": base_url,
        "model": cfg.ai_model,
        "reasoning_effort": cfg.ai_reasoning_effort,
        "assistant_model": cfg.assistant_model,
        "assistant_reasoning_effort": cfg.assistant_reasoning_effort,
        "codex_models": ["gpt-6-luna", "gpt-6-sol", "gpt-6-astra"],
        "codex_reasoning_efforts": ["low", "medium", "high", "xhigh", "max"],
        "api_key_configured": redact_api_key_present(cfg),
        "api_key_preview": api_key_preview(cfg.ai_api_key),
        "remote_allowed": cfg.ai_allow_remote_openai,
        "status": status,
        "warnings": warnings,
        "health_check": health_check,
        "registry": list_provider_specs(),
    }
