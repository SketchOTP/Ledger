"""Mark scheduled paychecks received — advance schedule only (checking updated manually)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlmodel import Session, select

from app.db.models import IncomeSource, Paycheck


def received_keys(session: Session) -> set[tuple[str, date]]:
    rows = session.exec(select(Paycheck)).all()
    return {(r.income_source_id, r.received_date) for r in rows if r.received_date}


def expand_income_occurrences_unpaid(
    source: IncomeSource,
    start: date,
    end: date,
    keys: set[tuple[str, date]],
) -> list[tuple[date, float]]:
    from app.core.cashflow_engine import _advance_income_date

    if not source.next_pay_date:
        return []
    freq = source.pay_frequency or "biweekly"
    d = source.next_pay_date
    while d < start:
        d = _advance_income_date(d, freq)
    out: list[tuple[date, float]] = []
    while d <= end:
        if (source.id, d) not in keys:
            out.append((d, float(source.amount)))
        d = _advance_income_date(d, freq)
    return out


def sum_received_in_range(session: Session, start: date, end: date) -> float:
    total = 0.0
    for row in session.exec(select(Paycheck)).all():
        if row.received_date and start <= row.received_date <= end:
            total += float(row.amount)
    return round(total, 2)


def sum_unreceived_scheduled_in_range(session: Session, start: date, end: date) -> float:
    keys = received_keys(session)
    total = 0.0
    for src in session.exec(select(IncomeSource)).all():
        for _, amount in expand_income_occurrences_unpaid(src, start, end, keys):
            total += amount
    return round(total, 2)


def income_events_in_range(session: Session, start: date, end: date) -> list[dict[str, Any]]:
    keys = received_keys(session)
    out: list[dict[str, Any]] = []
    for src in session.exec(select(IncomeSource)).all():
        for pay_date, amount in expand_income_occurrences_unpaid(src, start, end, keys):
            out.append(
                {
                    "name": src.source_name or "Paycheck",
                    "amount": amount,
                    "due_date": pay_date.isoformat(),
                    "kind": "income",
                    "income_source_id": src.id,
                    "can_mark_received": True,
                    "provenance": {
                        "rule": "income_schedule",
                        "type": "income",
                        "income_source_id": src.id,
                    },
                }
            )
    out.sort(key=lambda e: (e.get("due_date") or "", e.get("name") or ""))
    return out


def upcoming_income(session: Session, *, days: int = 30) -> list[dict[str, Any]]:
    today = date.today()
    end = today + timedelta(days=days)
    return income_events_in_range(session, today, end)


def income_api_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": row.get("name"),
        "amount": row.get("amount"),
        "due_date": row.get("due_date"),
        "kind": "income",
        "income_source_id": row.get("income_source_id"),
        "can_mark_received": True,
        "can_mark_paid": False,
    }


def _advance_income_anchor(source: IncomeSource, pay_date: date) -> date | None:
    from app.core.cashflow_engine import _advance_income_date

    freq = source.pay_frequency or "biweekly"
    if not source.next_pay_date:
        return None
    if source.next_pay_date <= pay_date:
        nxt = _advance_income_date(pay_date, freq)
        source.next_pay_date = nxt
        return nxt
    return source.next_pay_date


def mark_income_received(
    session: Session,
    source: IncomeSource,
    pay_date: date,
    *,
    received_on: date | None = None,
    amount: float | None = None,
) -> tuple[Paycheck, date | None]:
    keys = received_keys(session)
    if (source.id, pay_date) in keys:
        raise HTTPException(400, "Paycheck already marked received for this date")

    amt = float(amount if amount is not None else source.amount)
    marked_on = received_on or date.today()
    paycheck = Paycheck(
        income_source_id=source.id,
        amount=amt,
        received_date=pay_date,
        notes=f"Marked received {marked_on.isoformat()}",
    )
    session.add(paycheck)

    next_pay = _advance_income_anchor(source, pay_date)
    source.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(source)
    session.commit()
    session.refresh(paycheck)
    session.refresh(source)
    from app.core.payment_archive_log import log_payment_marked

    log_payment_marked(
        session,
        action="marked_received",
        kind="income",
        name=source.source_name or "Paycheck",
        amount=amt,
        scheduled_date=pay_date,
        marked_date=pay_date,
        income_source_id=source.id,
    )
    return paycheck, next_pay
