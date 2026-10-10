"""Legacy travel payment compatibility for preserved historical rows.

New production obligations use the generic misc payment path after D015.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlmodel import Session, select

from app.core.bill_schedule import bill_repeats, next_due_date_after_payment
from app.core.schedule_overdue import anchor_overdue_tuple, overdue_anchor_amount
from app.core.travel_schedule import expand_travel_occurrences, travel_display_name, travel_due_anchor
from app.db.models import TravelExpense, TravelOccurrence


def paid_keys(session: Session) -> set[tuple[str, date]]:
    rows = session.exec(select(TravelOccurrence).where(TravelOccurrence.paid.is_(True))).all()
    return {(r.travel_expense_id, r.due_date) for r in rows if r.due_date}


def occurrence_is_paid(travel_id: str, due: date, keys: set[tuple[str, date]]) -> bool:
    return (travel_id, due) in keys


def _overdue_anchor_amount(
    expense: TravelExpense,
    period_start: date,
    keys: set[tuple[str, date]],
) -> float:
    anchor = travel_due_anchor(expense, period_start)
    paid = occurrence_is_paid(expense.id, anchor, keys)
    amount = float(expense.amount or 0)
    return overdue_anchor_amount(anchor, period_start, amount, is_paid=paid)


def sum_travel_due_in_period_unpaid(
    session: Session,
    period_start: date,
    period_end: date,
) -> float:
    keys = paid_keys(session)
    total = 0.0
    for expense in session.exec(select(TravelExpense)).all():
        anchor = travel_due_anchor(expense, period_start)
        anchor_amt = _overdue_anchor_amount(expense, period_start, keys)
        total += anchor_amt
        for due, amount in expand_travel_occurrences(expense, period_start, period_end):
            if occurrence_is_paid(expense.id, due, keys):
                continue
            if due == anchor and anchor_amt > 0:
                continue
            total += amount
    return round(total, 2)


def travel_events_in_range_unpaid(
    expenses: list[TravelExpense],
    start: date,
    end: date,
    keys: set[tuple[str, date]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for expense in expenses:
        anchor = travel_due_anchor(expense, start)
        anchor_amt = _overdue_anchor_amount(expense, start, keys)
        name = travel_display_name(expense)
        if anchor_amt > 0:
            events.append(
                {
                    "date": anchor.isoformat(),
                    "type": "travel",
                    "name": name,
                    "amount": anchor_amt,
                    "travel_expense_id": expense.id,
                    "due_date": anchor.isoformat(),
                }
            )
        for due, amount in expand_travel_occurrences(expense, start, end):
            if occurrence_is_paid(expense.id, due, keys):
                continue
            if due == anchor and anchor_amt > 0:
                continue
            events.append(
                {
                    "date": due.isoformat(),
                    "type": "travel",
                    "name": name,
                    "amount": amount,
                    "travel_expense_id": expense.id,
                    "due_date": due.isoformat(),
                }
            )
    events.sort(key=lambda e: e["date"])
    return events


def list_unpaid_travel_past_due(session: Session, as_of: date | None = None) -> list[dict[str, Any]]:
    today = as_of or date.today()
    keys = paid_keys(session)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for expense in session.exec(select(TravelExpense)).all():
        anchor = travel_due_anchor(expense, today)
        amount = float(expense.amount or 0)
        anchor_row = anchor_overdue_tuple(
            anchor,
            today,
            amount,
            is_paid=occurrence_is_paid(expense.id, anchor, keys),
        )
        if anchor_row:
            due, amt = anchor_row
            out.append(
                {
                    "travel_expense_id": expense.id,
                    "name": travel_display_name(expense),
                    "amount": amt,
                    "due_date": due.isoformat(),
                    "days_overdue": (today - due).days,
                    "recurrence": expense.recurrence or "monthly",
                    "kind": "travel",
                }
            )
            seen.add((expense.id, due.isoformat()))

    out.sort(key=lambda x: (x["due_date"], x.get("name", "")))
    return out


def _advance_travel_anchor(expense: TravelExpense, paid_due: date, paid_on: date) -> date | None:
    r = (expense.recurrence or "monthly").lower()
    if not bill_repeats(r):
        expense.recurrence = "none"
        expense.due_date = None
        return None
    anchor = expense.due_date or paid_due
    nxt = next_due_date_after_payment(anchor, r, paid_on)
    if nxt is not None:
        expense.due_date = nxt
    return nxt


def mark_travel_paid(
    session: Session,
    expense: TravelExpense,
    due_date: date,
    *,
    paid_date: date | None = None,
    amount: float | None = None,
) -> tuple[TravelOccurrence, date | None]:
    paid_on = paid_date or date.today()
    amt = float(amount if amount is not None else expense.amount or 0)
    existing = session.exec(
        select(TravelOccurrence).where(
            TravelOccurrence.travel_expense_id == expense.id,
            TravelOccurrence.due_date == due_date,
        )
    ).first()
    if existing:
        existing.paid = True
        existing.paid_date = paid_on
        existing.amount = amt
        inst = existing
    else:
        inst = TravelOccurrence(
            travel_expense_id=expense.id,
            due_date=due_date,
            amount=amt,
            paid=True,
            paid_date=paid_on,
        )
    session.add(inst)
    next_due = _advance_travel_anchor(expense, due_date, paid_on)
    expense.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(expense)
    session.commit()
    session.refresh(inst)
    session.refresh(expense)
    from app.core.payment_archive_log import log_payment_marked

    log_payment_marked(
        session,
        action="marked_paid",
        kind="travel",
        name=expense.label or expense.expense_type or "Travel",
        amount=amt,
        scheduled_date=due_date,
        marked_date=paid_on,
        travel_expense_id=expense.id,
    )
    return inst, next_due
