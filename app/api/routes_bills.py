"""Bills API routes."""

import json
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.api.deps import get_db
from app.api.schemas import BillCreate, BillMarkPaid
from app.core.bill_deferral import bill_deferrals, record_approved_bill_deferral
from app.core.bill_payment import (
    bill_has_unpaid_past_due,
    bill_payment_summary,
    mark_bill_paid,
    paid_keys,
    pick_one_tap_occurrence,
    sync_bill_due_anchor,
    unmark_bill_paid,
)
from app.core.bill_schedule import bill_repeats, default_due_date, scheduled_bill_payments
from app.core.cashflow_engine import calculate_cashflow
from app.core.entry_validation import coerce_date_fields, raise_http_if_invalid, validate_bill
from app.core.unpaid_obligations import list_all_unpaid_past_due
from app.core.v2_opportunities import refresh_household_decision_dependents
from app.db.models import Bill, BillInstance
from app.events.bus import event_bus
from app.events.types import Event, EventType

router = APIRouter(prefix="/api/bills", tags=["bills"])


def _bill_payload(data: BillCreate) -> dict:
    payload = data.model_dump(exclude={"repeats", "payments"})
    if data.recurrence == "scheduled":
        payments = sorted(data.payments or [], key=lambda row: row.due_date)
        payload["schedule_json"] = json.dumps(
            [{"due_date": row.due_date.isoformat(), "amount": row.amount} for row in payments]
        )
        if payments:
            payload["due_date"] = payments[0].due_date
            payload["amount"] = payments[0].amount
    elif data.repeats is False:
        payload["recurrence"] = "once"
    elif data.repeats is True and (payload.get("recurrence") in (None, "", "once")):
        payload["recurrence"] = "monthly"
    if payload.get("due_date") is None and bill_repeats(payload.get("recurrence")):
        payload["due_date"] = default_due_date(payload.get("recurrence") or "monthly")
    if not payload.get("recurrence"):
        payload["recurrence"] = "monthly"
    if payload["recurrence"] != "scheduled":
        payload["schedule_json"] = "[]"
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


@router.get("/unpaid")
def unpaid_bills(session: Session = Depends(get_db)):
    """Past-due unpaid bill and debt occurrences for home-screen quick pay."""
    return {"items": list_all_unpaid_past_due(session)}


def _bill_list_sort_key(row: dict) -> tuple:
    """Soonest next due first; missing due dates last."""
    due = row.get("next_due_date") or row.get("due_date")
    return (
        due is None,
        due or "9999-12-31",
        (row.get("name") or "").lower(),
    )


@router.get("")
def list_bills(session: Session = Depends(get_db)):
    bills = session.exec(select(Bill)).all()
    rows = [bill_payment_summary(session, b) for b in bills]
    rows.sort(key=_bill_list_sort_key)
    return rows


@router.post("")
def create_bill(data: BillCreate, session: Session = Depends(get_db)):
    vr = validate_bill(data.model_dump())
    raise_http_if_invalid(vr)
    obj = Bill(**_bill_payload(data))
    session.add(obj)
    session.commit()
    session.refresh(obj)
    event_bus.emit(Event(event_type=EventType.BILL_CREATED.value, payload={"id": obj.id}))
    _check_due_events(session, obj)
    _emit_cashflow(session)
    return bill_payment_summary(session, obj)


@router.post("/{bill_id}/mark-paid")
def mark_paid(
    bill_id: str,
    data: BillMarkPaid,
    session: Session = Depends(get_db),
):
    bill = session.get(Bill, bill_id)
    if not bill:
        raise HTTPException(404, "Bill not found")
    payload = coerce_date_fields(data.model_dump(), "due_date", "paid_date")
    try:
        inst, next_due = mark_bill_paid(
            session,
            bill,
            payload["due_date"],
            paid_date=payload.get("paid_date"),
            amount=payload.get("amount"),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _emit_cashflow(session)
    return {
        "ok": True,
        "instance": inst,
        "next_due_date": next_due.isoformat() if next_due else None,
        "bill": bill_payment_summary(session, bill),
    }


@router.post("/{bill_id}/approved-deferral")
def approve_one_bill_date(bill_id: str, data: dict, session: Session = Depends(get_db)):
    bill = session.get(Bill, bill_id)
    if not bill:
        raise HTTPException(404, "Bill not found")
    try:
        original = date.fromisoformat(str(data.get("original_due_date") or ""))
        deferred = date.fromisoformat(str(data.get("deferred_due_date") or ""))
        record_approved_bill_deferral(
            session, bill, original_due_date=original, deferred_due_date=deferred,
            payee_approved=data.get("payee_approved") is True,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    _emit_cashflow(session)
    return bill_payment_summary(session, bill)


@router.post("/{bill_id}/mark-paid/quick")
def mark_paid_quick(bill_id: str, session: Session = Depends(get_db)):
    """One-tap: mark the most relevant unpaid occurrence for this bill."""
    bill = session.get(Bill, bill_id)
    if not bill:
        raise HTTPException(404, "Bill not found")
    keys = paid_keys(session)
    pick = pick_one_tap_occurrence(bill, keys)
    if not pick:
        raise HTTPException(400, "No unpaid occurrence to mark for this bill")
    due_date, amount = pick
    _, next_due = mark_bill_paid(session, bill, due_date, amount=amount)
    _emit_cashflow(session)
    return {
        "ok": True,
        "due_date": due_date.isoformat(),
        "next_due_date": next_due.isoformat() if next_due else None,
        "bill": bill_payment_summary(session, bill),
    }


@router.post("/{bill_id}/unmark-paid")
def unmark_paid(
    bill_id: str,
    data: BillMarkPaid,
    session: Session = Depends(get_db),
):
    payload = coerce_date_fields(data.model_dump(), "due_date")
    if not unmark_bill_paid(session, bill_id, payload["due_date"]):
        raise HTTPException(404, "Payment record not found")
    bill = session.get(Bill, bill_id)
    _emit_cashflow(session)
    return {
        "ok": True,
        "bill": bill_payment_summary(session, bill) if bill else None,
    }


@router.put("/{bill_id}")
def update_bill(bill_id: str, data: BillCreate, session: Session = Depends(get_db)):
    obj = session.get(Bill, bill_id)
    if not obj:
        raise HTTPException(404)
    vr = validate_bill(data.model_dump())
    raise_http_if_invalid(vr)
    active_deferral = bill_deferrals(session, bill_id).get((bill_id, obj.due_date)) if obj.due_date else None
    if active_deferral:
        proposed = _bill_payload(data)
        timing_fields = ("amount", "due_date", "recurrence", "schedule_json")
        if any(proposed.get(field) != getattr(obj, field) for field in timing_fields):
            raise HTTPException(400, "Pay or finish the approved one-time date before changing this bill's schedule")
    if obj.recurrence == "scheduled":
        paid = session.exec(
            select(BillInstance).where(BillInstance.bill_id == bill_id, BillInstance.paid.is_(True))
        ).all()
        if paid:
            new_payments = {
                (row.due_date, row.amount) for row in (data.payments or [])
            } if data.recurrence == "scheduled" else set()
            old_payments = dict(scheduled_bill_payments(obj))
            if any((row.due_date, old_payments.get(row.due_date)) not in new_payments for row in paid):
                raise HTTPException(400, "Keep already-paid dates and amounts when editing this plan.")
    for k, v in _bill_payload(data).items():
        setattr(obj, k, v)
    obj.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(obj)
    sync_bill_due_anchor(session, obj)
    session.commit()
    session.refresh(obj)
    event_bus.emit(Event(event_type=EventType.BILL_UPDATED.value, payload={"id": obj.id}))
    _check_due_events(session, obj)
    _emit_cashflow(session)
    return bill_payment_summary(session, obj)


@router.delete("/{bill_id}")
def delete_bill(bill_id: str, session: Session = Depends(get_db)):
    obj = session.get(Bill, bill_id)
    if not obj:
        raise HTTPException(404)
    session.delete(obj)
    session.commit()
    _emit_cashflow(session)
    return {"ok": True}


def _check_due_events(session: Session, bill: Bill) -> None:
    if not bill.due_date:
        return
    today = date.today()
    deferral = bill_deferrals(session, bill.id).get((bill.id, bill.due_date))
    effective_due = deferral.deferred_due_date if deferral else bill.due_date
    if bill_has_unpaid_past_due(session, bill, today):
        event_bus.emit(
            Event(
                event_type=EventType.BILL_OVERDUE.value,
                severity="critical",
                payload={"bill_id": bill.id},
            )
        )
    elif effective_due <= today + timedelta(days=3):
        event_bus.emit(
            Event(
                event_type=EventType.BILL_DUE_SOON.value,
                severity="warning",
                payload={"bill_id": bill.id},
            )
        )
