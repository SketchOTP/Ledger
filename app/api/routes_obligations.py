"""Unified mark-paid for all scheduled obligations."""

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import get_db
from app.core.cashflow_engine import calculate_cashflow
from app.core.entry_validation import coerce_date_fields
from app.core.obligation_mark_paid import mark_obligation_paid
from app.core.v2_opportunities import refresh_household_decision_dependents
from app.events.bus import event_bus
from app.events.types import Event, EventType

router = APIRouter(prefix="/api/obligations", tags=["obligations"])


@router.post("/mark-paid")
def mark_paid(data: dict, session: Session = Depends(get_db)):
    """Mark one scheduled obligation paid and advance its next due date."""
    payload = coerce_date_fields(data, "due_date", "paid_date", "date")
    if payload.get("date") and not payload.get("due_date"):
        payload["due_date"] = payload["date"]
    result = mark_obligation_paid(session, payload)
    cashflow = calculate_cashflow(session)
    refresh_household_decision_dependents(session, {"cashflow"}, cashflow=cashflow)
    event_bus.emit(
        Event(
            event_type=EventType.CASHFLOW_RECALCULATED.value,
            source="engine",
            payload=cashflow,
        )
    )
    return result
