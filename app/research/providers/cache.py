"""File-based research cache with TTL."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import settings

CACHE_DIR = settings.data_dir / "research_cache"
DEFAULT_TTL_HOURS = 24


def cache_path(provider_id: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{provider_id}.json"


def read_cache(provider_id: str, ttl_hours: int = DEFAULT_TTL_HOURS) -> dict[str, Any] | None:
    path = cache_path(provider_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(data.get("cached_at", "").replace("Z", ""))
        if datetime.now(UTC).replace(tzinfo=None) - fetched > timedelta(hours=ttl_hours):
            return None
        return data.get("payload")
    except (json.JSONDecodeError, ValueError, OSError):
        return None


def write_cache(provider_id: str, payload: dict[str, Any]) -> None:
    path = cache_path(provider_id)
    path.write_text(
        json.dumps({"cached_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "payload": payload}, indent=2),
        encoding="utf-8",
    )
