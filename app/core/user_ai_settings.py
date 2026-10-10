"""Per-user AI provider settings (stored beside household DB)."""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import settings
from app.db.user_db import user_db_path

_effective: ContextVar["EffectiveAISettings | None"] = ContextVar("effective_ai_settings", default=None)


@dataclass(frozen=True)
class EffectiveAISettings:
    ai_provider: str
    ai_base_url: str
    ai_api_key: str
    ai_model: str
    ai_allow_remote_openai: bool
    ai_reasoning_effort: str = "high"
    assistant_model: str = "gpt-6-luna"
    assistant_reasoning_effort: str = "high"


def _settings_path(user_id: str) -> Path:
    return user_db_path(user_id).parent / "ai_settings.json"


def load_user_ai_settings(user_id: str) -> dict[str, Any] | None:
    path = _settings_path(user_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_user_ai_settings(user_id: str, data: dict[str, Any]) -> dict[str, Any]:
    path = _settings_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def _from_global() -> EffectiveAISettings:
    return EffectiveAISettings(
        ai_provider=settings.ai_provider,
        ai_base_url=settings.ai_base_url,
        ai_api_key=settings.ai_api_key,
        ai_model=settings.ai_model,
        ai_allow_remote_openai=settings.ai_allow_remote_openai,
        ai_reasoning_effort="high",
    )


def _from_dict(data: dict[str, Any]) -> EffectiveAISettings:
    return EffectiveAISettings(
        ai_provider=str(data.get("ai_provider") or settings.ai_provider),
        ai_base_url=str(data.get("ai_base_url") or ""),
        ai_api_key=str(data.get("ai_api_key") or ""),
        ai_model=str(data.get("ai_model") or ""),
        ai_allow_remote_openai=bool(data.get("ai_allow_remote_openai", False)),
        ai_reasoning_effort=str(data.get("ai_reasoning_effort") or "high"),
        assistant_model=str(data.get("assistant_model") or "gpt-6-luna"),
        assistant_reasoning_effort=str(data.get("assistant_reasoning_effort") or "high"),
    )


def bind_user_ai_settings(user_id: str | None) -> None:
    if not user_id:
        _effective.set(None)
        return
    stored = load_user_ai_settings(user_id)
    if stored:
        _effective.set(_from_dict(stored))
    else:
        _effective.set(None)


def clear_user_ai_settings() -> None:
    _effective.set(None)


def get_effective_ai_settings() -> EffectiveAISettings:
    bound = _effective.get()
    if bound is not None:
        return bound
    return _from_global()
