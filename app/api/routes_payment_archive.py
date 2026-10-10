"""Payment archive — history of mark-paid / mark-received actions."""

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import get_db
from app.core.cashflow_engine import calculate_cashflow
from app.core.entry_validation import coerce_date_fields
from app.core.payment_archive import list_payment_archive
from app.core.payment_restore import restore_marked_payment
from app.core.v2_opportunities import refresh_household_decision_dependents
from app.events.bus import event_bus
from app.events.types import Event, EventType

router = APIRouter(prefix="/api/payment-archive", tags=["payment-archive"])


@router.get("")
def get_payment_archive(session: Session = Depends(get_db)):
    return {"items": list_payment_archive(session)}


@router.post("/restore")
def restore_payment(data: dict, session: Session = Depends(get_db)):
    payload = coerce_date_fields(data, "due_date", "pay_date", "date", "paid_date")
    if payload.get("date") and not payload.get("due_date"):
        payload["due_date"] = payload["date"]
    if payload.get("pay_date") and not payload.get("due_date"):
        payload["due_date"] = payload["pay_date"]
    result = restore_marked_payment(session, payload)
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
