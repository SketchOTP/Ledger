"""Per-occurrence debt minimum payments via DebtOccurrence; anchor due_date advances on pay."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from app.core.bill_schedule import bill_repeats, next_due_date_after_payment
from app.core.debt_schedule import expand_debt_occurrences
from app.db.models import Debt, DebtOccurrence


def paid_keys(session: Session) -> set[tuple[str, date]]:
    rows = session.exec(select(DebtOccurrence).where(DebtOccurrence.paid.is_(True))).all()
    return {(r.debt_id, r.due_date) for r in rows if r.due_date}


def _debt_id(debt: Debt | dict[str, Any]) -> str:
    return debt.id if isinstance(debt, Debt) else str(debt["id"])


def occurrence_is_paid(debt_id: str, due: date, keys: set[tuple[str, date]]) -> bool:
    return (debt_id, due) in keys


def _overdue_anchor_amount(
    debt: Debt,
    period_start: date,
    keys: set[tuple[str, date]],
) -> float:
    if not debt.due_date or debt.due_date >= period_start:
        return 0.0
    if occurrence_is_paid(debt.id, debt.due_date, keys):
        return 0.0
    return float(debt.minimum_payment or 0)


def expand_debt_occurrences_unpaid(
    debt: Debt | dict[str, Any],
    start: date,
    end: date,
    keys: set[tuple[str, date]],
) -> list[tuple[date, float]]:
    did = _debt_id(debt)
    return [
        (due, amount)
        for due, amount in expand_debt_occurrences(debt, start, end)
        if not occurrence_is_paid(did, due, keys)
    ]


def sum_debts_due_in_period_unpaid(
    session: Session,
    period_start: date,
    period_end: date,
) -> float:
    from app.core.debt_schedule import debts_for_cashflow

    events = debt_events_in_range_unpaid(
        debts_for_cashflow(session), period_start, period_end, paid_keys(session), session
    )
    return round(sum(float(row["amount"]) for row in events), 2)


def debt_events_in_range_unpaid(
    debts: list[Debt],
    start: date,
    end: date,
    keys: set[tuple[str, date]],
    session: Session | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for debt in debts:
        anchor_amt = _overdue_anchor_amount(debt, start, keys)
        if anchor_amt > 0 and debt.due_date:
            events.append(
                {
                    "date": debt.due_date.isoformat(),
                    "type": "debt",
                    "name": f"{debt.creditor} (minimum)",
                    "amount": anchor_amt,
                    "debt_id": debt.id,
                    "due_date": debt.due_date.isoformat(),
                }
            )
        for due, amount in expand_debt_occurrences_unpaid(debt, start, end, keys):
            if debt.due_date and due == debt.due_date and anchor_amt > 0:
                continue
            events.append(
                {
                    "date": due.isoformat(),
                    "type": "debt",
                    "name": f"{debt.creditor} (minimum)",
                    "amount": amount,
                    "debt_id": debt.id,
                    "due_date": due.isoformat(),
                }
            )
    if session is not None:
        from app.core.required_payment_deferral import apply_payment_deferrals

        return apply_payment_deferrals(session, "debt", events, end)
    events.sort(key=lambda e: e["date"])
    return events


def _history_start(debt: Debt, as_of: date) -> date:
    created = debt.created_at.date() if debt.created_at else as_of
    return min(created, as_of - timedelta(days=400))


def unpaid_occurrences_for_debt(
    debt: Debt,
    keys: set[tuple[str, date]],
    *,
    through: date,
    start: date | None = None,
) -> list[tuple[date, float]]:
    begin = start if start is not None else _history_start(debt, through)
    return expand_debt_occurrences_unpaid(debt, begin, through, keys)


def _anchor_overdue(debt: Debt, today: date, keys: set[tuple[str, date]]) -> tuple[date, float] | None:
    if not debt.due_date or debt.due_date >= today:
        return None
    if occurrence_is_paid(debt.id, debt.due_date, keys):
        return None
    return (debt.due_date, float(debt.minimum_payment or 0))


def list_unpaid_debt_past_due(session: Session, as_of: date | None = None) -> list[dict[str, Any]]:
    today = as_of or date.today()
    keys = paid_keys(session)
    from app.core.debt_schedule import debts_for_cashflow

    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for debt in debts_for_cashflow(session):
        anchor = _anchor_overdue(debt, today, keys)
        if anchor:
            due, amount = anchor
            out.append(
                {
                    "debt_id": debt.id,
                    "creditor": debt.creditor,
                    "name": debt.creditor,
                    "amount": amount,
                    "due_date": due.isoformat(),
                    "days_overdue": (today - due).days,
                    "recurrence": debt.recurrence or "monthly",
                    "kind": "debt",
                }
            )
            seen.add((debt.id, due.isoformat()))

        for due, amount in unpaid_occurrences_for_debt(
            debt, keys, through=today - timedelta(days=1)
        ):
            if due >= today:
                continue
            key = (debt.id, due.isoformat())
            if key in seen:
                continue
            out.append(
                {
                    "debt_id": debt.id,
                    "creditor": debt.creditor,
                    "name": debt.creditor,
                    "amount": amount,
                    "due_date": due.isoformat(),
                    "days_overdue": (today - due).days,
                    "recurrence": debt.recurrence or "monthly",
                    "kind": "debt",
                }
            )
            seen.add(key)

    from app.core.required_payment_deferral import apply_past_due_deferrals

    return apply_past_due_deferrals(session, "debt", out, today)


def pick_one_tap_occurrence(
    debt: Debt,
    keys: set[tuple[str, date]],
    as_of: date | None = None,
) -> tuple[date, float] | None:
    today = as_of or date.today()
    anchor = _anchor_overdue(debt, today + timedelta(days=1), keys)
    if anchor:
        return anchor
    if debt.due_date and not occurrence_is_paid(debt.id, debt.due_date, keys):
        if debt.due_date <= today + timedelta(days=60):
            return (debt.due_date, float(debt.minimum_payment or 0))
    future = expand_debt_occurrences_unpaid(debt, today, today + timedelta(days=60), keys)
    if future:
        return future[0]
    return None


def debt_has_unpaid_past_due(session: Session, debt: Debt, as_of: date | None = None) -> bool:
    return any(row["debt_id"] == debt.id for row in list_unpaid_debt_past_due(session, as_of))


def _advance_debt_anchor(debt: Debt, paid_due: date, paid_on: date) -> date | None:
    r = (debt.recurrence or "monthly").lower()
    if not bill_repeats(r):
        debt.recurrence = "none"
        debt.due_date = None
        return None
    nxt = next_due_date_after_payment(paid_due, r, paid_on)
    if nxt is not None:
        debt.due_date = nxt
    return nxt


def mark_debt_paid(
    session: Session,
    debt: Debt,
    due_date: date,
    *,
    paid_date: date | None = None,
    amount: float | None = None,
) -> tuple[DebtOccurrence, date | None]:
    from app.core.required_payment_deferral import original_payment_date

    due_date = original_payment_date(session, "debt", debt.id, due_date)
    paid_on = paid_date or date.today()
    amt = float(amount if amount is not None else debt.minimum_payment or 0)
    existing = session.exec(
        select(DebtOccurrence).where(
            DebtOccurrence.debt_id == debt.id,
            DebtOccurrence.due_date == due_date,
        )
    ).first()
    if existing:
        existing.paid = True
        existing.paid_date = paid_on
        existing.amount = amt
        inst = existing
    else:
        inst = DebtOccurrence(
            debt_id=debt.id,
            due_date=due_date,
            amount=amt,
            paid=True,
            paid_date=paid_on,
        )
    session.add(inst)

    next_due = _advance_debt_anchor(debt, due_date, paid_on)
    debt.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(debt)
    session.commit()
    session.refresh(inst)
    session.refresh(debt)
    from app.core.payment_archive_log import log_payment_marked

    log_payment_marked(
        session,
        action="marked_paid",
        kind="debt",
        name=debt.creditor,
        amount=amt,
        scheduled_date=due_date,
        marked_date=paid_on,
        debt_id=debt.id,
    )
    return inst, next_due


def unmark_debt_paid(session: Session, debt_id: str, due_date: date) -> bool:
    debt = session.get(Debt, debt_id)
    inst = session.exec(
        select(DebtOccurrence).where(
            DebtOccurrence.debt_id == debt_id,
            DebtOccurrence.due_date == due_date,
        )
    ).first()
    if not inst:
        return False
    session.delete(inst)
    if debt and debt.due_date and bill_repeats(debt.recurrence or "monthly"):
        if debt.due_date > due_date:
            debt.due_date = due_date
            debt.updated_at = datetime.now(UTC).replace(tzinfo=None)
            session.add(debt)
    session.commit()
    return True


def debt_payment_summary(session: Session, debt: Debt) -> dict[str, Any]:
    from app.core.required_payment_deferral import current_payment_deferral

    keys = paid_keys(session)
    today = date.today()
    past_unpaid = [
        {"due_date": row["due_date"], "amount": row["amount"]}
        for row in list_unpaid_debt_past_due(session, today)
        if row["debt_id"] == debt.id
    ]
    one_tap = pick_one_tap_occurrence(debt, keys, today)
    approved = current_payment_deferral(session, "debt", debt.id, debt.due_date)
    next_due = approved.deferred_due_date.isoformat() if approved else (
        debt.due_date.isoformat() if debt.due_date else None
    )
    return {
        "id": debt.id,
        "creditor": debt.creditor,
        "balance": debt.balance,
        "apr": debt.apr,
        "minimum_payment": debt.minimum_payment,
        "due_date": next_due,
        "next_due_date": next_due,
        "approved_deferral": (
            {"original_due_date": approved.original_due_date.isoformat(), "deferred_due_date": next_due}
            if approved else None
        ),
        "recurrence": debt.recurrence or "monthly",
        "debt_type": debt.debt_type,
        "payoff_priority": debt.payoff_priority,
        "hardship_notes": debt.hardship_notes,
        "created_at": debt.created_at.isoformat() if debt.created_at else None,
        "updated_at": debt.updated_at.isoformat() if debt.updated_at else None,
        "unpaid_past_due": past_unpaid,
        "one_tap": (
            {"due_date": one_tap[0].isoformat(), "amount": one_tap[1]} if one_tap else None
        ),
    }
