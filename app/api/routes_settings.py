"""Application settings API."""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.ai.provider_registry import ProviderConfigurationError, build_ai_settings_payload
from app.ai.providers import get_provider
from app.ai.providers.cli import CODEX_MODELS, CODEX_REASONING_EFFORTS
from app.api.deps import AUTH_DISABLED, CurrentUser
from app.core.user_ai_settings import bind_user_ai_settings, load_user_ai_settings, save_user_ai_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


class AISettingsSave(BaseModel):
    ai_provider: str = Field(..., min_length=1)
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    ai_allow_remote_openai: bool = False
    ai_reasoning_effort: str = "high"
    assistant_model: str = "gpt-6-luna"
    assistant_reasoning_effort: str = "high"


def _user_id_for_ai(user: CurrentUser) -> str:
    return user.id if not AUTH_DISABLED else "dev-local"


@router.get("/ai")
def get_ai_settings(user: CurrentUser):
    """AI provider configuration status (never returns raw API keys)."""
    bind_user_ai_settings(_user_id_for_ai(user))
    health: dict[str, Any] = {}
    try:
        health = get_provider().health_check()
    except ProviderConfigurationError as exc:
        health = {"ok": False, "error": str(exc)}
    except Exception as exc:
        health = {"ok": False, "error": str(exc)}
    return build_ai_settings_payload(health_check=health)


@router.post("/ai")
def save_ai_settings(body: AISettingsSave, user: CurrentUser):
    """Save per-user AI provider settings and run a health check."""
    user_id = _user_id_for_ai(user)
    if (body.ai_reasoning_effort not in CODEX_REASONING_EFFORTS
            or body.assistant_reasoning_effort not in CODEX_REASONING_EFFORTS
            or body.assistant_model not in CODEX_MODELS
            or (body.ai_provider == "future_cli_codex" and
                (body.ai_model not in CODEX_MODELS or body.ai_base_url.strip()))):
        raise HTTPException(status_code=422, detail="Choose a supported Codex model and reasoning effort.")
    existing = load_user_ai_settings(user_id) or {}
    api_key = "" if body.ai_provider == "future_cli_codex" else body.ai_api_key.strip()
    if body.ai_provider != "future_cli_codex" and not api_key and existing.get("ai_api_key"):
        api_key = str(existing["ai_api_key"])
    payload = {
        "ai_provider": body.ai_provider.strip().lower(),
        "ai_base_url": body.ai_base_url.strip(),
        "ai_api_key": api_key,
        "ai_model": body.ai_model.strip() or str(existing.get("ai_model") or ""),
        "ai_allow_remote_openai": body.ai_allow_remote_openai,
        "ai_reasoning_effort": body.ai_reasoning_effort,
        "assistant_model": body.assistant_model,
        "assistant_reasoning_effort": body.assistant_reasoning_effort,
    }
    save_user_ai_settings(user_id, payload)
    bind_user_ai_settings(user_id)

    try:
        health = get_provider().health_check()
    except ProviderConfigurationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": str(exc), "errors": [str(exc)]},
        ) from exc
    except Exception as exc:
        health = {"ok": False, "error": str(exc)}

    result = build_ai_settings_payload(health_check=health)
    if not health.get("ok"):
        raise HTTPException(
            status_code=422,
            detail={
                "message": "AI provider saved but health check failed",
                "errors": [str(health.get("error") or "unavailable")],
                "settings": result,
            },
        )
    return result
