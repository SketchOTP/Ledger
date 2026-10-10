"""Recurring miscellaneous spending — monthly obligations and cashflow events."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlmodel import Session, select

from app.core.bill_schedule import bill_repeats, dates_in_range
from app.core.misc_catalog import misc_category_label
from app.db.models import MiscSpending


def misc_due_anchor(item: MiscSpending | dict[str, Any], range_start: date) -> date:
    if isinstance(item, MiscSpending):
        due = item.due_date
    else:
        due = item.get("due_date")
        if isinstance(due, str):
            due = date.fromisoformat(due[:10])
    if isinstance(due, date):
        return due
    day = min(20, 28)
    return range_start.replace(day=day)


def _misc_recurrence(item: MiscSpending | dict[str, Any]) -> str:
    if isinstance(item, MiscSpending):
        return (item.recurrence or "monthly").lower()
    return (item.get("recurrence") or "monthly").lower()


def expand_misc_occurrences(
    item: MiscSpending | dict[str, Any],
    start: date,
    end: date,
) -> list[tuple[date, float]]:
    if (item.active if isinstance(item, MiscSpending) else item.get("active", True)) is False:
        return []
    if isinstance(item, MiscSpending):
        amount = float(item.amount or 0)
        name = item.name
    else:
        amount = float(item.get("amount", 0))
        name = item.get("name", "")
    if amount <= 0 or not str(name).strip():
        return []
    recurrence = _misc_recurrence(item)
    anchor = misc_due_anchor(item, start)
    if not bill_repeats(recurrence):
        if start <= anchor <= end:
            return [(anchor, amount)]
        return []
    return [(d, amount) for d in dates_in_range(start, end, anchor, recurrence)]


def misc_display_name(item: MiscSpending) -> str:
    base = (item.name or "").strip()
    if base:
        return base
    return misc_category_label(item.category)


def sum_misc_due_in_period(session: Session, period_start: date, period_end: date) -> float:
    from app.core.misc_payment import sum_misc_due_in_period_unpaid

    return sum_misc_due_in_period_unpaid(session, period_start, period_end)


def misc_events_in_range(
    items: list[MiscSpending],
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in items:
        for due, amount in expand_misc_occurrences(item, start, end):
            events.append(
                {
                    "date": due.isoformat(),
                    "type": "misc",
                    "name": misc_display_name(item),
                    "amount": amount,
                    "misc_spending_id": item.id,
                    "category": item.category,
                }
            )
    events.sort(key=lambda e: e["date"])
    return events


def monthly_misc_total(session: Session) -> float:
    total = 0.0
    for item in session.exec(select(MiscSpending)).all():
        if not item.active:
            continue
        if not bill_repeats(_misc_recurrence(item)):
            continue
        amt = float(item.amount or 0)
        r = _misc_recurrence(item)
        if r == "weekly":
            total += amt * 52 / 12
        elif r == "biweekly":
            total += amt * 26 / 12
        elif r == "quarterly":
            total += amt / 3
        elif r == "annual":
            total += amt / 12
        else:
            total += amt
    return round(total, 2)
