"""Research API key settings — per-user market data credentials."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import AUTH_DISABLED, CurrentUser
from app.core.api_key_preview import api_key_preview
from app.core.user_research_settings import (
    RESEARCH_KEY_FIELDS,
    load_user_research_settings,
    research_settings_status,
    save_user_research_settings,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ResearchSettingsSave(BaseModel):
    FRED_API_KEY: str = ""
    EIA_API_KEY: str = ""
    BLS_API_KEY: str = ""
    USDA_ERS_API_KEY: str = ""


def _user_id(user: CurrentUser) -> str:
    return user.id if not AUTH_DISABLED else "dev-local"


@router.get("/research")
def get_research_settings(user: CurrentUser):
    uid = _user_id(user)
    stored = load_user_research_settings(uid)
    fields = []
    for key in RESEARCH_KEY_FIELDS:
        val = str(stored.get(key) or "").strip()
        fields.append(
            {
                "key": key,
                "configured": bool(val),
                "has_value": bool(val),
                "preview": api_key_preview(val),
            }
        )
    return {"fields": fields, "status": research_settings_status(uid)}


@router.post("/research")
def save_research_settings(body: ResearchSettingsSave, user: CurrentUser):
    uid = _user_id(user)
    existing = load_user_research_settings(uid)
    payload: dict[str, Any] = {}
    for key in RESEARCH_KEY_FIELDS:
        incoming = getattr(body, key, "").strip()
        if incoming:
            payload[key] = incoming
        elif existing.get(key):
            payload[key] = str(existing[key])
        else:
            payload[key] = ""
    save_user_research_settings(uid, payload)
    return get_research_settings(user)
