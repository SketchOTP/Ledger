"""Miscellaneous spending API."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.api.deps import get_db
from app.api.routes_bills import _bill_payload
from app.api.schemas import BillCreate, MiscSpendingCreate
from app.core.bill_schedule import bill_repeats, default_due_date
from app.core.cashflow_engine import calculate_cashflow
from app.core.entry_validation import coerce_date_fields, raise_http_if_invalid, validate_bill, validate_misc_spending
from app.core.misc_catalog import misc_catalog
from app.core.misc_payment import mark_misc_paid
from app.core.v2_opportunities import refresh_household_decision_dependents
from app.db.models import Bill, MiscSpending
from app.events.bus import event_bus
from app.events.types import Event, EventType

router = APIRouter(prefix="/api/misc", tags=["misc"])


def _expense_payload(data: MiscSpendingCreate) -> dict:
    payload = data.model_dump(exclude={"repeats"})
    if data.repeats is False:
        payload["recurrence"] = "once"
    elif data.repeats is True and (payload.get("recurrence") in (None, "", "once")):
        payload["recurrence"] = "monthly"
    if payload.get("due_date") is None and bill_repeats(payload.get("recurrence")):
        payload["due_date"] = default_due_date(payload.get("recurrence") or "monthly")
    if not payload.get("recurrence"):
        payload["recurrence"] = "monthly"
    payload["name"] = str(payload.get("name") or "").strip()
    payload["category"] = str(payload.get("category") or "other").lower().strip()
    return payload


def _emit_cashflow(session: Session) -> None:
    cashflow = calculate_cashflow(session)
    refresh_household_decision_dependents(session, {"cashflow"}, cashflow=cashflow)
    event_bus.emit(
        Event(
            event_type=EventType.CASHFLOW_RECALCULATED.value,
            source="engine",
            payload=cashflow,
        )
    )


@router.get("/catalog")
def get_catalog():
    return {"categories": misc_catalog()}


@router.get("")
def list_misc(session: Session = Depends(get_db)):
    items = list(session.exec(
        select(MiscSpending).where(MiscSpending.active.is_(True)).order_by(MiscSpending.created_at.asc())
    ).all())
    return {"items": items, "catalog": misc_catalog()}


@router.post("/{item_id}/move-to-bills")
def move_misc_to_bills(item_id: str, data: BillCreate, session: Session = Depends(get_db)):
    """Reclassify a current item atomically; retain its payment history as archived misc."""
    item = session.get(MiscSpending, item_id)
    if item is None or not item.active:
        raise HTTPException(404, "Spending item not found.")
    vr = validate_bill(data.model_dump())
    raise_http_if_invalid(vr)
    bill = Bill(**_bill_payload(data))
    item.active = False
    item.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(bill)
    session.add(item)
    session.commit()
    session.refresh(bill)
    _emit_cashflow(session)
    return {"bill_id": bill.id, "archived_misc_id": item.id}


@router.post("")
def create_misc(data: MiscSpendingCreate, session: Session = Depends(get_db)):
    vr = validate_misc_spending(data.model_dump())
    raise_http_if_invalid(vr)
    payload = coerce_date_fields(_expense_payload(data), "due_date")
    obj = MiscSpending(**payload)
    session.add(obj)
    session.commit()
    session.refresh(obj)
    _emit_cashflow(session)
    return obj


@router.put("/{item_id}")
def update_misc(item_id: str, data: MiscSpendingCreate, session: Session = Depends(get_db)):
    obj = session.get(MiscSpending, item_id)
    if not obj or not obj.active:
        raise HTTPException(404)
    vr = validate_misc_spending(data.model_dump())
    raise_http_if_invalid(vr)
    payload = coerce_date_fields(_expense_payload(data), "due_date")
    for k, v in payload.items():
        setattr(obj, k, v)
    obj.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(obj)
    session.commit()
    session.refresh(obj)
    _emit_cashflow(session)
    return obj


@router.post("/{item_id}/mark-paid")
def mark_misc_item_paid(item_id: str, data: dict, session: Session = Depends(get_db)):
    obj = session.get(MiscSpending, item_id)
    if not obj or not obj.active:
        raise HTTPException(404, "Misc item not found")
    payload = coerce_date_fields(data, "due_date", "paid_date")
    due = payload.get("due_date")
    if not due:
        raise HTTPException(400, "due_date is required")
    inst, next_due = mark_misc_paid(
        session,
        obj,
        due,
        paid_date=payload.get("paid_date"),
        amount=payload.get("amount"),
    )
    _emit_cashflow(session)
    return {
        "ok": True,
        "instance": inst,
        "next_due_date": next_due.isoformat() if next_due else None,
    }


@router.delete("/{item_id}")
def delete_misc(item_id: str, session: Session = Depends(get_db)):
    obj = session.get(MiscSpending, item_id)
    if not obj:
        raise HTTPException(404)
    session.delete(obj)
    session.commit()
    _emit_cashflow(session)
    return {"ok": True}
