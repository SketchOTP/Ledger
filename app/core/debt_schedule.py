"""Schedule debt minimum payments on monthly due dates (like recurring bills)."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlmodel import Session, select

from app.core.bill_schedule import bill_repeats, dates_in_range
from app.db.models import CreditCard, Debt, MortgageAccount


def debts_for_cashflow(session: Session) -> list[Debt]:
    """Debts scheduled as monthly payments; skip mortgage and cards mirrored in credit_cards."""
    debts = list(session.exec(select(Debt)).all())
    card_issuers = {
        (c.issuer or "").lower()
        for c in session.exec(select(CreditCard)).all()
    }
    has_mortgage_account = bool(session.exec(select(MortgageAccount)).first())
    skip_types = frozenset({"mortgage", "home_loan", "home loan"})
    card_types = frozenset({"credit_card", "card"})
    out: list[Debt] = []
    for d in debts:
        dt = (d.debt_type or "").lower()
        if has_mortgage_account and dt in skip_types:
            continue
        if dt in card_types and (d.creditor or "").lower() in card_issuers:
            continue
        out.append(d)
    return out


def debt_due_anchor(debt: Debt | dict[str, Any], range_start: date) -> date:
    if isinstance(debt, Debt):
        due = debt.due_date
    else:
        due = debt.get("due_date")
        if isinstance(due, str):
            due = date.fromisoformat(due[:10])
    if isinstance(due, date):
        return due
    # Default: 15th of current month (or range start month)
    day = 15
    return range_start.replace(day=min(day, 28))


def _debt_recurrence(debt: Debt | dict[str, Any]) -> str:
    if isinstance(debt, Debt):
        return (debt.recurrence or "monthly").lower()
    return (debt.get("recurrence") or "monthly").lower()


def expand_debt_occurrences(
    debt: Debt | dict[str, Any],
    start: date,
    end: date,
) -> list[tuple[date, float]]:
    if isinstance(debt, Debt):
        pay = float(debt.minimum_payment or 0)
    else:
        pay = float(debt.get("minimum_payment", 0))
    if pay <= 0:
        return []
    recurrence = _debt_recurrence(debt)
    anchor = debt_due_anchor(debt, start)
    if not bill_repeats(recurrence):
        if start <= anchor <= end:
            return [(anchor, pay)]
        return []
    return [(d, pay) for d in dates_in_range(start, end, anchor, recurrence)]


def sum_debts_due_in_period(session: Session, period_start: date, period_end: date) -> float:
    from app.core.debt_payment import sum_debts_due_in_period_unpaid

    return sum_debts_due_in_period_unpaid(session, period_start, period_end)


def debt_events_in_range(
    debts: list[Debt],
    start: date,
    end: date,
    session: Session | None = None,
) -> list[dict[str, Any]]:
    if session is not None:
        from app.core.debt_payment import debt_events_in_range_unpaid, paid_keys

        return debt_events_in_range_unpaid(debts, start, end, paid_keys(session), session)

    events: list[dict[str, Any]] = []
    for debt in debts:
        for due, amount in expand_debt_occurrences(debt, start, end):
            events.append(
                {
                    "date": due.isoformat(),
                    "type": "debt",
                    "name": f"{debt.creditor} (minimum)",
                    "amount": amount,
                    "debt_id": debt.id,
                }
            )
    events.sort(key=lambda e: e["date"])
    return events


def monthly_debt_minimums_total(session: Session) -> float:
    total = 0.0
    for debt in debts_for_cashflow(session):
        if not bill_repeats(_debt_recurrence(debt)):
            continue
        amt = float(debt.minimum_payment or 0)
        r = _debt_recurrence(debt)
        if r == "weekly":
            total += amt * 52 / 12
        elif r == "biweekly":
            total += amt * 26 / 12
        else:
            total += amt
    return round(total, 2)
