"""Per-user market research API keys (stored beside household DB)."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.db.user_db import user_db_path

RESEARCH_KEY_FIELDS = (
    "FRED_API_KEY",
    "EIA_API_KEY",
    "BLS_API_KEY",
    "USDA_ERS_API_KEY",
)


def _settings_path(user_id: str) -> Path:
    return user_db_path(user_id).parent / "research_settings.json"


def load_user_research_settings(user_id: str) -> dict[str, Any]:
    path = _settings_path(user_id)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_user_research_settings(user_id: str, data: dict[str, Any]) -> dict[str, str]:
    path = _settings_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned: dict[str, str] = {}
    existing = load_user_research_settings(user_id)
    for key in RESEARCH_KEY_FIELDS:
        raw = data.get(key)
        if raw is None:
            cleaned[key] = str(existing.get(key) or "")
        else:
            cleaned[key] = str(raw).strip()
    path.write_text(json.dumps(cleaned, indent=2), encoding="utf-8")
    return cleaned


def research_settings_status(user_id: str) -> dict[str, Any]:
    stored = load_user_research_settings(user_id)
    fields = []
    for key in RESEARCH_KEY_FIELDS:
        val = (stored.get(key) or os.getenv(key, "")).strip()
        fields.append({"key": key, "configured": bool(val)})
    return {"fields": fields}


@contextmanager
def user_research_env(user_id: str | None) -> Iterator[None]:
    if not user_id:
        yield
        return
    stored = load_user_research_settings(user_id)
    previous: dict[str, str | None] = {}
    for key in RESEARCH_KEY_FIELDS:
        val = str(stored.get(key) or "").strip()
        if val:
            previous[key] = os.environ.get(key)
            os.environ[key] = val
    try:
        yield
    finally:
        for key, old in previous.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
