"""Per-occurrence mortgage payments via MortgageOccurrence."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from app.core.bill_schedule import bill_repeats, next_due_date_after_payment
from app.core.mortgage_schedule import (
    expand_mortgage_occurrences,
    mortgage_due_by_anchor,
    mortgage_payment_amount,
)
from app.db.models import MortgageAccount, MortgageOccurrence


def paid_keys(session: Session) -> set[tuple[str, date]]:
    rows = session.exec(select(MortgageOccurrence).where(MortgageOccurrence.paid.is_(True))).all()
    return {(r.mortgage_id, r.due_date) for r in rows if r.due_date}


def occurrence_is_paid(mortgage_id: str, due: date, keys: set[tuple[str, date]]) -> bool:
    return (mortgage_id, due) in keys


def _mortgage_anchor_due(mortgage: MortgageAccount, range_start: date) -> date | None:
    anchor = mortgage_due_by_anchor(mortgage, range_start)
    return anchor if isinstance(anchor, date) else None


def _overdue_anchor_amount(
    mortgage: MortgageAccount,
    period_start: date,
    keys: set[tuple[str, date]],
) -> float:
    due = _mortgage_anchor_due(mortgage, period_start)
    if due is None or due >= period_start:
        return 0.0
    if occurrence_is_paid(mortgage.id, due, keys):
        return 0.0
    pay = mortgage_payment_amount(mortgage)
    return pay if pay > 0 else 0.0


def sum_mortgages_due_in_period_unpaid(
    session: Session,
    period_start: date,
    period_end: date,
) -> float:
    events = mortgage_events_in_range_unpaid(
        list(session.exec(select(MortgageAccount)).all()),
        period_start,
        period_end,
        paid_keys(session),
        session,
    )
    return round(sum(float(row["amount"]) for row in events), 2)


def mortgage_events_in_range_unpaid(
    mortgages: list[MortgageAccount],
    start: date,
    end: date,
    keys: set[tuple[str, date]],
    session: Session | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for mortgage in mortgages:
        anchor = _mortgage_anchor_due(mortgage, start)
        anchor_amt = _overdue_anchor_amount(mortgage, start, keys)
        if anchor_amt > 0 and anchor is not None:
            events.append(
                {
                    "date": anchor.isoformat(),
                    "type": "mortgage",
                    "name": f"{mortgage.lender} (mortgage)",
                    "amount": anchor_amt,
                    "mortgage_id": mortgage.id,
                    "due_date": anchor.isoformat(),
                }
            )
        for due, amount in expand_mortgage_occurrences(mortgage, start, end):
            if occurrence_is_paid(mortgage.id, due, keys):
                continue
            if anchor and due == anchor and anchor_amt > 0:
                continue
            events.append(
                {
                    "date": due.isoformat(),
                    "type": "mortgage",
                    "name": f"{mortgage.lender} (mortgage)",
                    "amount": amount,
                    "mortgage_id": mortgage.id,
                    "due_date": due.isoformat(),
                }
            )
    if session is not None:
        from app.core.required_payment_deferral import apply_payment_deferrals

        return apply_payment_deferrals(session, "mortgage", events, end)
    events.sort(key=lambda e: e["date"])
    return events


def _history_start(mortgage: MortgageAccount, as_of: date) -> date:
    created = mortgage.created_at.date() if mortgage.created_at else as_of
    return min(created, as_of - timedelta(days=400))


def unpaid_occurrences_for_mortgage(
    mortgage: MortgageAccount,
    keys: set[tuple[str, date]],
    *,
    through: date,
    start: date | None = None,
) -> list[tuple[date, float]]:
    begin = start if start is not None else _history_start(mortgage, through)
    out: list[tuple[date, float]] = []
    for due, amount in expand_mortgage_occurrences(mortgage, begin, through):
        if not occurrence_is_paid(mortgage.id, due, keys):
            out.append((due, amount))
    return out


def _anchor_overdue(
    mortgage: MortgageAccount,
    today: date,
    keys: set[tuple[str, date]],
) -> tuple[date, float] | None:
    due = _mortgage_anchor_due(mortgage, today)
    if due is None or due >= today:
        return None
    if occurrence_is_paid(mortgage.id, due, keys):
        return None
    pay = mortgage_payment_amount(mortgage)
    if pay <= 0:
        return None
    return (due, pay)


def list_unpaid_mortgage_past_due(session: Session, as_of: date | None = None) -> list[dict[str, Any]]:
    """Unpaid mortgage occurrences before today — driven by due_by_date anchor."""
    today = as_of or date.today()
    keys = paid_keys(session)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for mortgage in session.exec(select(MortgageAccount)).all():
        anchor = _anchor_overdue(mortgage, today, keys)
        if anchor:
            due, amount = anchor
            out.append(
                {
                    "mortgage_id": mortgage.id,
                    "name": f"{mortgage.lender} (mortgage)",
                    "amount": amount,
                    "due_date": due.isoformat(),
                    "days_overdue": (today - due).days,
                    "recurrence": mortgage.recurrence or "monthly",
                    "kind": "mortgage",
                }
            )
            seen.add((mortgage.id, due.isoformat()))

        for due, amount in unpaid_occurrences_for_mortgage(
            mortgage, keys, through=today - timedelta(days=1)
        ):
            if due >= today:
                continue
            key = (mortgage.id, due.isoformat())
            if key in seen:
                continue
            out.append(
                {
                    "mortgage_id": mortgage.id,
                    "name": f"{mortgage.lender} (mortgage)",
                    "amount": amount,
                    "due_date": due.isoformat(),
                    "days_overdue": (today - due).days,
                    "recurrence": mortgage.recurrence or "monthly",
                    "kind": "mortgage",
                }
            )
            seen.add(key)

    from app.core.required_payment_deferral import apply_past_due_deferrals

    return apply_past_due_deferrals(session, "mortgage", out, today)


def _advance_mortgage_anchor(mortgage: MortgageAccount, paid_due: date, paid_on: date) -> date | None:
    r = (mortgage.recurrence or "monthly").lower()
    if not bill_repeats(r):
        return None
    anchor = mortgage.due_by_date or paid_due
    nxt = next_due_date_after_payment(anchor, r, paid_on)
    if nxt is not None:
        mortgage.due_by_date = nxt
    return nxt


def mark_mortgage_paid(
    session: Session,
    mortgage: MortgageAccount,
    due_date: date,
    *,
    paid_date: date | None = None,
    amount: float | None = None,
) -> tuple[MortgageOccurrence, date | None]:
    from app.core.required_payment_deferral import original_payment_date

    due_date = original_payment_date(session, "mortgage", mortgage.id, due_date)
    paid_on = paid_date or date.today()
    amt = float(amount if amount is not None else mortgage_payment_amount(mortgage))
    existing = session.exec(
        select(MortgageOccurrence).where(
            MortgageOccurrence.mortgage_id == mortgage.id,
            MortgageOccurrence.due_date == due_date,
        )
    ).first()
    if existing:
        existing.paid = True
        existing.paid_date = paid_on
        existing.amount = amt
        inst = existing
    else:
        inst = MortgageOccurrence(
            mortgage_id=mortgage.id,
            due_date=due_date,
            amount=amt,
            paid=True,
            paid_date=paid_on,
        )
    session.add(inst)

    next_due = _advance_mortgage_anchor(mortgage, due_date, paid_on)
    session.add(mortgage)
    session.commit()
    session.refresh(inst)
    session.refresh(mortgage)
    from app.core.payment_archive_log import log_payment_marked

    log_payment_marked(
        session,
        action="marked_paid",
        kind="mortgage",
        name=f"{mortgage.lender} (mortgage)",
        amount=amt,
        scheduled_date=due_date,
        marked_date=paid_on,
        mortgage_id=mortgage.id,
    )
    return inst, next_due
