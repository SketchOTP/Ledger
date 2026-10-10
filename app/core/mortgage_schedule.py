"""Schedule mortgage payments (P&I + escrow) on due-by dates — separate from Bills."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlmodel import Session, select

from app.core.bill_schedule import bill_repeats, dates_in_range
from app.db.models import MortgageAccount


def mortgage_payment_amount(mortgage: MortgageAccount | dict[str, Any]) -> float:
    if isinstance(mortgage, MortgageAccount):
        return float(mortgage.monthly_payment or 0) + float(mortgage.escrow_amount or 0)
    return float(mortgage.get("monthly_payment", 0)) + float(mortgage.get("escrow_amount", 0))


def mortgage_due_by_anchor(mortgage: MortgageAccount | dict[str, Any], range_start: date) -> date:
    """Cashflow / commitment anchor — when payment should be made by."""
    if isinstance(mortgage, MortgageAccount):
        anchor = mortgage.due_by_date or mortgage.start_date
    else:
        anchor = mortgage.get("due_by_date") or mortgage.get("start_date")
        if isinstance(anchor, str):
            anchor = date.fromisoformat(anchor[:10])
    if isinstance(anchor, date):
        return anchor
    return range_start.replace(day=1)


def mortgage_late_by_anchor(mortgage: MortgageAccount | dict[str, Any]) -> date | None:
    """Grace end date for the current cycle (AI / risk — not used for conservative cashflow)."""
    if isinstance(mortgage, MortgageAccount):
        late = mortgage.late_by_date
        due = mortgage.due_by_date or mortgage.start_date
    else:
        late = mortgage.get("late_by_date")
        due = mortgage.get("due_by_date") or mortgage.get("start_date")
        if isinstance(late, str):
            late = date.fromisoformat(late[:10])
        if isinstance(due, str):
            due = date.fromisoformat(due[:10])
    if isinstance(late, date):
        return late
    return None


def mortgage_schedule_meta(mortgage: MortgageAccount | dict[str, Any]) -> dict[str, Any]:
    due = mortgage_due_by_anchor(mortgage, date.today())
    late = mortgage_late_by_anchor(mortgage)
    grace_days = (late - due).days if late and late >= due else None
    return {
        "due_by_date": due.isoformat(),
        "late_by_date": late.isoformat() if late else None,
        "grace_days_after_due_by": grace_days,
        "cashflow_uses_due_by": True,
        "note": (
            "Payment is scheduled on due_by_date for cashflow. "
            "late_by_date is grace before the obligation is treated as late."
        ),
    }


def _mortgage_recurrence(mortgage: MortgageAccount | dict[str, Any]) -> str:
    if isinstance(mortgage, MortgageAccount):
        return (mortgage.recurrence or "monthly").lower()
    return (mortgage.get("recurrence") or "monthly").lower()


def expand_mortgage_occurrences(
    mortgage: MortgageAccount | dict[str, Any],
    start: date,
    end: date,
) -> list[tuple[date, float]]:
    pay = mortgage_payment_amount(mortgage)
    if pay <= 0:
        return []
    recurrence = _mortgage_recurrence(mortgage)
    anchor = mortgage_due_by_anchor(mortgage, start)
    if not bill_repeats(recurrence):
        if start <= anchor <= end:
            return [(anchor, pay)]
        return []
    return [(d, pay) for d in dates_in_range(start, end, anchor, recurrence)]


def sum_mortgages_due_in_period(session: Session, period_start: date, period_end: date) -> float:
    from app.core.mortgage_payment import sum_mortgages_due_in_period_unpaid

    return sum_mortgages_due_in_period_unpaid(session, period_start, period_end)


def mortgage_events_in_range(
    mortgages: list[MortgageAccount],
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for mortgage in mortgages:
        meta = mortgage_schedule_meta(mortgage)
        for due, amount in expand_mortgage_occurrences(mortgage, start, end):
            events.append(
                {
                    "date": due.isoformat(),
                    "type": "mortgage",
                    "name": f"{mortgage.lender} (mortgage)",
                    "amount": amount,
                    "mortgage_id": mortgage.id,
                    "due_by_date": meta["due_by_date"],
                    "late_by_date": meta["late_by_date"],
                    "grace_days_after_due_by": meta["grace_days_after_due_by"],
                }
            )
    events.sort(key=lambda e: e["date"])
    return events


def monthly_mortgage_payments_total(session: Session) -> float:
    total = 0.0
    for mortgage in session.exec(select(MortgageAccount)).all():
        if not bill_repeats(_mortgage_recurrence(mortgage)):
            continue
        amt = mortgage_payment_amount(mortgage)
        r = _mortgage_recurrence(mortgage)
        if r == "weekly":
            total += amt * 52 / 12
        elif r == "biweekly":
            total += amt * 26 / 12
        else:
            total += amt
    return round(total, 2)


def mortgage_is_late(mortgage: MortgageAccount, as_of: date | None = None) -> bool:
    today = as_of or date.today()
    late = mortgage_late_by_anchor(mortgage)
    if late:
        return today > late
    due = mortgage_due_by_anchor(mortgage, today)
    return today > due
