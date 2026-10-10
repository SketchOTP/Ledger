"""Events API."""

import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc
from sqlmodel import Session, select

from app.api.deps import get_db
from app.config import settings
from app.db.models import EventLogEntry

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("")
def list_events(
    limit: int = Query(100, le=500),
    event_type: str | None = None,
    session: Session = Depends(get_db),
):
    q = select(EventLogEntry).order_by(desc(EventLogEntry.created_at))
    entries = list(session.exec(q).all())[:limit]
    if event_type:
        entries = [e for e in entries if e.event_type == event_type]
    return [
        {
            "id": e.id,
            "event_id": e.event_id,
            "event_type": e.event_type,
            "created_at": e.created_at.isoformat(),
            "source": e.source,
            "payload": json.loads(e.payload_json),
            "correlation_id": e.correlation_id,
            "severity": e.severity,
        }
        for e in entries
    ]


@router.get("/files")
def list_event_files():
    log_dir = settings.event_log_dir
    if not log_dir.exists():
        return []
    return [p.name for p in sorted(log_dir.glob("events-*.jsonl"), reverse=True)]
