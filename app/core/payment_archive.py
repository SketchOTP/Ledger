"""Historical audit list of mark-paid, mark-received, and restore actions."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from app.core.payment_archive_log import (
    archive_event_to_row,
    backfill_legacy_archive_events,
    is_mark_active,
)
from app.db.models import PaymentArchiveEvent


def list_payment_archive(session: Session, *, limit: int = 500) -> list[dict[str, Any]]:
    """Permanent audit trail — every mark and restore event, newest first."""
    backfill_legacy_archive_events(session)
    events = session.exec(
        select(PaymentArchiveEvent).order_by(PaymentArchiveEvent.created_at.desc())
    ).all()
    rows: list[dict[str, Any]] = []
    for event in events:
        active = is_mark_active(session, event) if event.action in ("marked_paid", "marked_received") else None
        rows.append(archive_event_to_row(event, active=active))
    if limit > 0:
        rows = rows[:limit]
    return rows
