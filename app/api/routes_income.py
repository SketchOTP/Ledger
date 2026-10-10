"""Income API routes."""

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.api.deps import get_db
from app.api.schemas import IncomeSourceCreate
from app.core.cashflow_engine import calculate_cashflow
from app.core.entry_validation import coerce_date_fields, raise_http_if_invalid, validate_paycheck
from app.core.income_aggregate import sync_borrower_income_from_sources
from app.core.income_payment import mark_income_received
from app.core.v2_opportunities import refresh_household_decision_dependents
from app.db.models import IncomeSource, Paycheck
from app.events.bus import event_bus
from app.events.types import Event, EventType

router = APIRouter(prefix="/api/income", tags=["income"])


@router.get("/sources")
def list_sources(session: Session = Depends(get_db)):
    return session.exec(select(IncomeSource)).all()


@router.post("/sources")
def create_source(data: IncomeSourceCreate, session: Session = Depends(get_db)):
    vr = validate_paycheck(data.model_dump())
    raise_http_if_invalid(vr)
    obj = IncomeSource(**data.model_dump())
    session.add(obj)
    session.commit()
    session.refresh(obj)
    sync_borrower_income_from_sources(session)
    refresh_household_decision_dependents(session, {"cashflow"})
    event_bus.emit(Event(event_type=EventType.INCOME_CREATED.value, payload={"id": obj.id}))
    event_bus.emit(Event(
        event_type=EventType.CASHFLOW_RECALCULATED.value,
        source="engine",
        payload=calculate_cashflow(session),
    ))
    return obj


@router.put("/sources/{source_id}")
def update_source(source_id: str, data: IncomeSourceCreate, session: Session = Depends(get_db)):
    obj = session.get(IncomeSource, source_id)
    if not obj:
        raise HTTPException(404)
    for k, v in data.model_dump().items():
        setattr(obj, k, v)
    obj.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(obj)
    session.commit()
    sync_borrower_income_from_sources(session)
    refresh_household_decision_dependents(session, {"cashflow"})
    event_bus.emit(Event(event_type=EventType.INCOME_UPDATED.value, payload={"id": obj.id}))
    return obj


@router.delete("/sources/{source_id}")
def delete_source(source_id: str, session: Session = Depends(get_db)):
    obj = session.get(IncomeSource, source_id)
    if not obj:
        raise HTTPException(404)
    session.delete(obj)
    session.commit()
    sync_borrower_income_from_sources(session)
    refresh_household_decision_dependents(session, {"cashflow"})
    return {"ok": True}


@router.get("/paychecks")
def list_paychecks(session: Session = Depends(get_db)):
    return session.exec(select(Paycheck)).all()


@router.post("/mark-received")
def mark_received(data: dict, session: Session = Depends(get_db)):
    """Mark a scheduled paycheck received — advance schedule only (update checking yourself)."""
    payload = coerce_date_fields(data, "pay_date", "due_date", "received_date")
    pay_raw = payload.get("pay_date") or payload.get("due_date")
    if not pay_raw:
        raise HTTPException(400, "pay_date is required")
    pay_date = pay_raw if isinstance(pay_raw, date) else date.fromisoformat(str(pay_raw)[:10])
    received_raw = payload.get("received_date")
    received_on = (
        received_raw
        if isinstance(received_raw, date)
        else (date.fromisoformat(str(received_raw)[:10]) if received_raw else None)
    )
    source_id = payload.get("income_source_id")
    if not source_id:
        raise HTTPException(400, "income_source_id is required")
    source = session.get(IncomeSource, source_id)
    if not source:
        raise HTTPException(404, "Income source not found")
    amount = payload.get("amount")
    paycheck, next_pay = mark_income_received(
        session,
        source,
        pay_date,
        received_on=received_on,
        amount=float(amount) if amount is not None else None,
    )
    event_bus.emit(
        Event(
            event_type=EventType.PAYCHECK_RECEIVED.value,
            payload={"id": paycheck.id, "income_source_id": source.id},
        )
    )
    refresh_household_decision_dependents(session, {"cashflow"})
    event_bus.emit(
        Event(
            event_type=EventType.CASHFLOW_RECALCULATED.value,
            source="engine",
            payload=calculate_cashflow(session),
        )
    )
    return {
        "ok": True,
        "kind": "income",
        "paycheck_id": paycheck.id,
        "next_pay_date": next_pay.isoformat() if next_pay else None,
        "checking_credited": paycheck.amount,
    }


@router.post("/paychecks")
def create_paycheck(
    income_source_id: str,
    amount: float,
    received_date: str,
    session: Session = Depends(get_db),
):
    from datetime import date
    obj = Paycheck(
        income_source_id=income_source_id,
        amount=amount,
        received_date=date.fromisoformat(received_date),
    )
    session.add(obj)
    session.commit()
    refresh_household_decision_dependents(session, {"cashflow"})
    event_bus.emit(Event(event_type=EventType.PAYCHECK_RECEIVED.value, payload={"id": obj.id}))
    return obj
