"""Daily opportunity cache with web research + user save/dismiss state."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, select

from app.db.models import OpportunityDailyCache, OpportunityUserState


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def opportunity_key(item: dict[str, Any]) -> str:
    raw = f"{item.get('category', '')}|{item.get('title', '')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def list_opportunities_for_user(session: Session, user_id: str | None = None) -> list[dict[str, Any]]:
    cache = session.get(OpportunityDailyCache, "current")
    if not cache:
        return []
    try:
        items = json.loads(cache.opportunities_json or "[]")
    except json.JSONDecodeError:
        items = []
    states = {
        row.opportunity_key: row.status
        for row in session.exec(select(OpportunityUserState)).all()
    }
    out: list[dict[str, Any]] = []
    for item in items:
        key = opportunity_key(item)
        status = states.get(key)
        if status == "dismissed":
            continue
        enriched = dict(item)
        enriched["opportunity_key"] = key
        enriched["user_status"] = status or "active"
        out.append(enriched)
    return out


def set_opportunity_state(
    session: Session,
    key: str,
    status: str,
    *,
    title: str = "",
    category: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in ("saved", "dismissed"):
        raise ValueError("status must be saved or dismissed")
    row = session.get(OpportunityUserState, key)
    now = _now()
    if row:
        row.status = status
        row.updated_at = now
        if title:
            row.title = title
        if category:
            row.category = category
        if payload is not None:
            row.payload_json = json.dumps(payload, default=str)
    else:
        row = OpportunityUserState(
            opportunity_key=key,
            status=status,
            title=title,
            category=category,
            payload_json=json.dumps(payload or {}, default=str),
            created_at=now,
            updated_at=now,
        )
    session.add(row)
    session.commit()
    return {"ok": True, "opportunity_key": key, "status": status}


def list_saved_opportunities(session: Session) -> list[dict[str, Any]]:
    rows = list(
        session.exec(
            select(OpportunityUserState).where(OpportunityUserState.status == "saved")
        ).all()
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row.payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        out.append(
            {
                "opportunity_key": row.opportunity_key,
                "title": row.title or payload.get("title", ""),
                "category": row.category or payload.get("category", ""),
                "payload": payload,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        )
    return out
