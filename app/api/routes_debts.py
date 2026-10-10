"""Debts API routes."""

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.api.deps import get_db
from app.api.schemas import BillMarkPaid, DebtCreate
from app.core.cashflow_engine import calculate_cashflow
from app.core.debt_optimizer import rank_debts
from app.core.debt_payment import (
    debt_payment_summary,
    mark_debt_paid,
    paid_keys,
    pick_one_tap_occurrence,
    unmark_debt_paid,
)
from app.core.entry_validation import raise_http_if_invalid, validate_debt
from app.core.required_payment_deferral import current_payment_deferral, record_approved_payment_deferral
from app.core.v2_opportunities import refresh_household_decision_dependents
from app.db.models import CreditCard, Debt
from app.events.bus import event_bus
from app.events.types import Event, EventType

router = APIRouter(prefix="/api/debts", tags=["debts"])


def _ensure_credit_card_from_debt(session: Session, debt: Debt) -> None:
    """Mirror credit-card debts into credit_cards so the Cards page stays in sync."""
    existing = session.exec(
        select(CreditCard).where(CreditCard.issuer == debt.creditor)
    ).first()
    if existing:
        existing.balance = debt.balance
        existing.apr = debt.apr
        existing.minimum_payment = debt.minimum_payment
        if existing.credit_limit > 0:
            existing.utilization_percent = round(debt.balance / existing.credit_limit * 100, 1)
        session.add(existing)
        return
    limit = max(debt.balance * 1.5, debt.balance + 1000, 1000)
    card = CreditCard(
        issuer=debt.creditor,
        card_name=debt.creditor,
        balance=debt.balance,
        credit_limit=limit,
        apr=debt.apr,
        minimum_payment=debt.minimum_payment,
        due_date=debt.due_date,
        utilization_percent=round(debt.balance / limit * 100, 1) if limit else None,
    )
    session.add(card)


def _emit_cashflow(session: Session) -> None:
    cashflow = calculate_cashflow(session)
    refresh_household_decision_dependents(session, {"debt"}, cashflow=cashflow)
    event_bus.emit(
        Event(
            event_type=EventType.CASHFLOW_RECALCULATED.value,
            payload=cashflow,
        )
    )


@router.get("")
def list_debts(session: Session = Depends(get_db)):
    debts = session.exec(select(Debt)).all()
    return [debt_payment_summary(session, d) for d in debts]


@router.get("/strategy/{strategy}")
def debt_strategy(strategy: str, session: Session = Depends(get_db)):
    return rank_debts(session, strategy)


@router.post("")
def create_debt(data: DebtCreate, session: Session = Depends(get_db)):
    vr = validate_debt(data.model_dump())
    raise_http_if_invalid(vr)
    obj = Debt(**data.model_dump())
    session.add(obj)
    session.flush()
    if obj.debt_type in ("credit_card", "card"):
        _ensure_credit_card_from_debt(session, obj)
    session.commit()
    session.refresh(obj)
    event_bus.emit(Event(event_type=EventType.DEBT_CREATED.value, payload={"id": obj.id}))
    _emit_cashflow(session)
    return debt_payment_summary(session, obj)


@router.post("/{debt_id}/mark-paid")
def mark_paid(
    debt_id: str,
    data: BillMarkPaid,
    session: Session = Depends(get_db),
):
    from app.core.entry_validation import coerce_date_fields

    debt = session.get(Debt, debt_id)
    if not debt:
        raise HTTPException(404, "Debt not found")
    payload = coerce_date_fields(data.model_dump(), "due_date", "paid_date")
    _, next_due = mark_debt_paid(
        session,
        debt,
        payload["due_date"],
        paid_date=payload.get("paid_date"),
        amount=payload.get("amount"),
    )
    _emit_cashflow(session)
    return {
        "ok": True,
        "next_due_date": next_due.isoformat() if next_due else None,
        "debt": debt_payment_summary(session, debt),
    }


@router.post("/{debt_id}/approved-deferral")
def approve_one_debt_date(debt_id: str, data: dict, session: Session = Depends(get_db)):
    debt = session.get(Debt, debt_id)
    if not debt:
        raise HTTPException(404, "Debt not found")
    if debt.debt_type in ("credit_card", "card"):
        mirrored = session.exec(select(CreditCard).where(CreditCard.issuer == debt.creditor)).first()
        if mirrored:
            raise HTTPException(400, "Record this payment's approved date in Credit cards")
    try:
        record_approved_payment_deferral(
            session, "debt", debt_id,
            original_due_date=date.fromisoformat(str(data.get("original_due_date") or "")),
            deferred_due_date=date.fromisoformat(str(data.get("deferred_due_date") or "")),
            payee_approved=data.get("payee_approved") is True,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    _emit_cashflow(session)
    return debt_payment_summary(session, debt)


@router.post("/{debt_id}/mark-paid/quick")
def mark_paid_quick(debt_id: str, session: Session = Depends(get_db)):
    debt = session.get(Debt, debt_id)
    if not debt:
        raise HTTPException(404, "Debt not found")
    keys = paid_keys(session)
    pick = pick_one_tap_occurrence(debt, keys)
    if not pick:
        raise HTTPException(400, "No unpaid occurrence to mark for this debt")
    due_date, amount = pick
    _, next_due = mark_debt_paid(session, debt, due_date, amount=amount)
    _emit_cashflow(session)
    return {
        "ok": True,
        "due_date": due_date.isoformat(),
        "next_due_date": next_due.isoformat() if next_due else None,
        "debt": debt_payment_summary(session, debt),
    }


@router.post("/{debt_id}/unmark-paid")
def unmark_paid(
    debt_id: str,
    data: BillMarkPaid,
    session: Session = Depends(get_db),
):
    from app.core.entry_validation import coerce_date_fields

    payload = coerce_date_fields(data.model_dump(), "due_date")
    if not unmark_debt_paid(session, debt_id, payload["due_date"]):
        raise HTTPException(404, "Payment record not found")
    debt = session.get(Debt, debt_id)
    _emit_cashflow(session)
    return {
        "ok": True,
        "debt": debt_payment_summary(session, debt) if debt else None,
    }


@router.put("/{debt_id}")
def update_debt(debt_id: str, data: DebtCreate, session: Session = Depends(get_db)):
    obj = session.get(Debt, debt_id)
    if not obj:
        raise HTTPException(404)
    vr = validate_debt(data.model_dump())
    raise_http_if_invalid(vr)
    if current_payment_deferral(session, "debt", debt_id, obj.due_date):
        timing_fields = ("due_date", "recurrence", "minimum_payment")
        if any(data.model_dump().get(key) != getattr(obj, key) for key in timing_fields):
            raise HTTPException(400, "Pay the approved one-time payment before changing its schedule")
    for k, v in data.model_dump().items():
        setattr(obj, k, v)
    obj.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(obj)
    if obj.debt_type in ("credit_card", "card"):
        _ensure_credit_card_from_debt(session, obj)
    session.commit()
    event_bus.emit(Event(event_type=EventType.DEBT_UPDATED.value, payload={"id": obj.id}))
    _emit_cashflow(session)
    return debt_payment_summary(session, obj)


@router.delete("/{debt_id}")
def delete_debt(debt_id: str, session: Session = Depends(get_db)):
    obj = session.get(Debt, debt_id)
    if not obj:
        raise HTTPException(404)
    session.delete(obj)
    session.commit()
    _emit_cashflow(session)
    return {"ok": True}
