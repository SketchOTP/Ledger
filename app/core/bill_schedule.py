"""Recurring bill scheduling — CFO treats bills as repeating obligations."""

from __future__ import annotations

import calendar
import json
from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from app.db.models import Bill

RECURRENCE_CHOICES = frozenset({"weekly", "biweekly", "monthly", "quarterly", "annual", "once", "none", "scheduled"})


def bill_repeats(recurrence: str | None) -> bool:
    r = (recurrence or "monthly").lower()
    return r not in ("once", "none", "scheduled", "")


def recurrence_label(recurrence: str | None) -> str:
    labels = {
        "weekly": "Every week",
        "biweekly": "Every 2 weeks",
        "monthly": "Every month",
        "quarterly": "Every quarter",
        "annual": "Once a year",
        "once": "One time only",
        "none": "Does not repeat",
        "scheduled": "Exact payment dates",
    }
    return labels.get((recurrence or "monthly").lower(), "Every month")


def default_due_date(recurrence: str = "monthly") -> date:
    """Sensible default when user does not pick a date."""
    today = date.today()
    if not bill_repeats(recurrence):
        return today + timedelta(days=7)
    # Next month same day (clamped), or 1st of next month
    day = min(today.day, 28)
    month = today.month % 12 + 1
    year = today.year + (1 if today.month == 12 else 0)
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def advance_anchor(anchor: date, recurrence: str) -> date:
    r = recurrence.lower()
    if r == "weekly":
        return anchor + timedelta(days=7)
    if r == "biweekly":
        return anchor + timedelta(days=14)
    if r == "quarterly":
        return _advance_months(anchor, 3)
    if r == "annual":
        return _advance_months(anchor, 12)
    return _advance_months(anchor, 1)


def next_due_date_after_payment(
    paid_due: date,
    recurrence: str | None,
    as_of: date | None = None,
) -> date | None:
    """
    Next scheduled due date after a payment for paid_due.

    For repeating bills, advances one cycle at a time until the due date is
    on or after as_of (default today) so assessments use the upcoming obligation.
    """
    today = as_of or date.today()
    r = (recurrence or "monthly").lower()
    if not bill_repeats(r):
        return None
    nxt = advance_anchor(paid_due, r)
    for _ in range(120):
        if nxt >= today:
            return nxt
        nxt = advance_anchor(nxt, r)
    return nxt


def _advance_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def dates_in_range(start: date, end: date, anchor: date, recurrence: str) -> list[date]:
    """All occurrence dates for a bill between start and end (inclusive)."""
    r = (recurrence or "monthly").lower()
    if r in ("once", "none"):
        if start <= anchor <= end:
            return [anchor]
        return []

    if r == "weekly":
        cursor = anchor
        while cursor < start:
            cursor += timedelta(days=7)
        out: list[date] = []
        while cursor <= end:
            out.append(cursor)
            cursor += timedelta(days=7)
        return out

    if r == "biweekly":
        cursor = anchor
        while cursor < start:
            cursor += timedelta(days=14)
        out = []
        while cursor <= end:
            out.append(cursor)
            cursor += timedelta(days=14)
        return out

    cursor = anchor
    step = 1 if r == "monthly" else 3 if r == "quarterly" else 12
    while cursor < start:
        cursor = _advance_months(cursor, step)
    out = []
    while cursor <= end:
        out.append(cursor)
        cursor = _advance_months(cursor, step)
    return out


def _bill_anchor(bill: Bill | dict[str, Any], range_start: date) -> date:
    if isinstance(bill, Bill):
        due = bill.due_date
        recurrence = bill.recurrence or "monthly"
    else:
        due = bill.get("due_date")
        recurrence = bill.get("recurrence") or "monthly"
        if isinstance(due, str):
            due = date.fromisoformat(due[:10])
    if isinstance(due, date):
        return due
    if bill_repeats(recurrence):
        return range_start.replace(day=1)
    return range_start + timedelta(days=7)


def scheduled_bill_payments(bill: Bill | dict[str, Any]) -> list[tuple[date, float]]:
    raw = bill.schedule_json if isinstance(bill, Bill) else bill.get("schedule_json") or "[]"
    return sorted(
        [(date.fromisoformat(row["due_date"]), float(row["amount"])) for row in json.loads(raw)],
        key=lambda row: row[0],
    )


def expand_bill_occurrences(
    bill: Bill | dict[str, Any],
    start: date,
    end: date,
) -> list[tuple[date, float]]:
    if isinstance(bill, Bill):
        if not bill.required:
            return []
        amount = float(bill.amount)
        recurrence = bill.recurrence or "monthly"
    else:
        if not bill.get("required", True):
            return []
        amount = float(bill.get("amount", 0))
        recurrence = bill.get("recurrence") or "monthly"

    if recurrence == "scheduled":
        return [
            (due, payment_amount)
            for due, payment_amount in scheduled_bill_payments(bill)
            if start <= due <= end
        ]

    anchor = _bill_anchor(bill, start)
    return [(d, amount) for d in dates_in_range(start, end, anchor, recurrence)]


def sum_bills_due_in_period(session: Session, period_start: date, period_end: date) -> float:
    from app.core.bill_payment import sum_bills_due_in_period_unpaid

    return sum_bills_due_in_period_unpaid(session, period_start, period_end)


def bill_events_in_range(
    bills: list[Bill],
    start: date,
    end: date,
    session: Session | None = None,
) -> list[dict[str, Any]]:
    if session is not None:
        from app.core.bill_payment import bill_events_in_range_unpaid, paid_keys

        return bill_events_in_range_unpaid(bills, start, end, paid_keys(session), session)

    events: list[dict[str, Any]] = []
    for bill in bills:
        for due, amount in expand_bill_occurrences(bill, start, end):
            events.append(
                {
                    "date": due.isoformat(),
                    "type": "bill",
                    "name": bill.name,
                    "amount": amount,
                    "required": bill.required,
                    "recurrence": bill.recurrence,
                    "repeats": bill_repeats(bill.recurrence),
                }
            )
    events.sort(key=lambda e: e["date"])
    return events


def monthly_obligation_total(session: Session) -> float:
    """Total monthly weight of all required bills (for CFO summaries)."""
    total = 0.0
    for bill in session.exec(select(Bill)).all():
        if not bill.required:
            continue
        r = (bill.recurrence or "monthly").lower()
        amt = float(bill.amount)
        if r == "weekly":
            total += amt * 52 / 12
        elif r == "biweekly":
            total += amt * 26 / 12
        elif r == "monthly":
            total += amt
        elif r == "quarterly":
            total += amt / 3
        elif r == "annual":
            total += amt / 12
        elif r in ("once", "scheduled"):
            total += 0
        else:
            total += amt
    return round(total, 2)
