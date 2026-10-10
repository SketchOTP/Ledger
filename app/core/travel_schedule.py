"""Legacy travel scheduling retained only for archive/payment compatibility.

Canonical V2 planning uses ``MiscSpending`` after the D015 migration.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlmodel import Session, select

from app.core.bill_schedule import bill_repeats, dates_in_range
from app.db.models import TravelExpense


def travel_due_anchor(expense: TravelExpense | dict[str, Any], range_start: date) -> date:
    if isinstance(expense, TravelExpense):
        due = expense.due_date
    else:
        due = expense.get("due_date")
        if isinstance(due, str):
            due = date.fromisoformat(due[:10])
    if isinstance(due, date):
        return due
    day = min(15, 28)
    return range_start.replace(day=day)


def _travel_recurrence(expense: TravelExpense | dict[str, Any]) -> str:
    if isinstance(expense, TravelExpense):
        return (expense.recurrence or "monthly").lower()
    return (expense.get("recurrence") or "monthly").lower()


def expand_travel_occurrences(
    expense: TravelExpense | dict[str, Any],
    start: date,
    end: date,
) -> list[tuple[date, float]]:
    if isinstance(expense, TravelExpense):
        amount = float(expense.amount or 0)
    else:
        amount = float(expense.get("amount", 0))
    if amount <= 0:
        return []
    recurrence = _travel_recurrence(expense)
    anchor = travel_due_anchor(expense, start)
    if not bill_repeats(recurrence):
        if start <= anchor <= end:
            return [(anchor, amount)]
        return []
    return [(d, amount) for d in dates_in_range(start, end, anchor, recurrence)]


def travel_display_name(expense: TravelExpense) -> str:
    base = (expense.label or "").strip()
    if base:
        return base
    mode = (expense.mode or "travel").replace("_", " ").title()
    kind = (expense.expense_type or "expense").replace("_", " ").title()
    return f"{mode} {kind}"


def sum_travel_due_in_period(session: Session, period_start: date, period_end: date) -> float:
    from app.core.travel_payment import sum_travel_due_in_period_unpaid

    return sum_travel_due_in_period_unpaid(session, period_start, period_end)


def travel_events_in_range(
    expenses: list[TravelExpense],
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for expense in expenses:
        for due, amount in expand_travel_occurrences(expense, start, end):
            events.append(
                {
                    "date": due.isoformat(),
                    "type": "travel",
                    "name": travel_display_name(expense),
                    "amount": amount,
                    "travel_expense_id": expense.id,
                    "mode": expense.mode,
                }
            )
    events.sort(key=lambda e: e["date"])
    return events


def monthly_travel_total(session: Session) -> float:
    total = 0.0
    for expense in session.exec(select(TravelExpense)).all():
        if not bill_repeats(_travel_recurrence(expense)):
            continue
        amt = float(expense.amount or 0)
        r = _travel_recurrence(expense)
        if r == "weekly":
            total += amt * 52 / 12
        elif r == "biweekly":
            total += amt * 26 / 12
        else:
            total += amt
    return round(total, 2)
