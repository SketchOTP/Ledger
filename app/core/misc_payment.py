"""Per-occurrence misc spending payments via MiscOccurrence."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlmodel import Session, select

from app.core.bill_schedule import bill_repeats, next_due_date_after_payment
from app.core.misc_schedule import expand_misc_occurrences, misc_display_name, misc_due_anchor
from app.core.schedule_overdue import anchor_overdue_tuple, overdue_anchor_amount
from app.db.models import MiscOccurrence, MiscSpending


def paid_keys(session: Session) -> set[tuple[str, date]]:
    rows = session.exec(select(MiscOccurrence).where(MiscOccurrence.paid.is_(True))).all()
    return {(r.misc_spending_id, r.due_date) for r in rows if r.due_date}


def occurrence_is_paid(misc_id: str, due: date, keys: set[tuple[str, date]]) -> bool:
    return (misc_id, due) in keys


def _overdue_anchor_amount(
    item: MiscSpending,
    period_start: date,
    keys: set[tuple[str, date]],
) -> float:
    if not item.active:
        return 0.0
    anchor = misc_due_anchor(item, period_start)
    paid = occurrence_is_paid(item.id, anchor, keys)
    amount = float(item.amount or 0)
    return overdue_anchor_amount(anchor, period_start, amount, is_paid=paid)


def sum_misc_due_in_period_unpaid(
    session: Session,
    period_start: date,
    period_end: date,
) -> float:
    keys = paid_keys(session)
    total = 0.0
    for item in session.exec(select(MiscSpending)).all():
        if not item.active:
            continue
        anchor = misc_due_anchor(item, period_start)
        anchor_amt = _overdue_anchor_amount(item, period_start, keys)
        total += anchor_amt
        for due, amount in expand_misc_occurrences(item, period_start, period_end):
            if occurrence_is_paid(item.id, due, keys):
                continue
            if due == anchor and anchor_amt > 0:
                continue
            total += amount
    return round(total, 2)


def misc_events_in_range_unpaid(
    items: list[MiscSpending],
    start: date,
    end: date,
    keys: set[tuple[str, date]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in items:
        if not item.active:
            continue
        anchor = misc_due_anchor(item, start)
        anchor_amt = _overdue_anchor_amount(item, start, keys)
        name = misc_display_name(item)
        if anchor_amt > 0:
            events.append(
                {
                    "date": anchor.isoformat(),
                    "type": "misc",
                    "name": name,
                    "amount": anchor_amt,
                    "misc_spending_id": item.id,
                    "due_date": anchor.isoformat(),
                    "category": item.category,
                }
            )
        for due, amount in expand_misc_occurrences(item, start, end):
            if occurrence_is_paid(item.id, due, keys):
                continue
            if due == anchor and anchor_amt > 0:
                continue
            events.append(
                {
                    "date": due.isoformat(),
                    "type": "misc",
                    "name": name,
                    "amount": amount,
                    "misc_spending_id": item.id,
                    "due_date": due.isoformat(),
                    "category": item.category,
                }
            )
    events.sort(key=lambda e: e["date"])
    return events


def list_unpaid_misc_past_due(session: Session, as_of: date | None = None) -> list[dict[str, Any]]:
    today = as_of or date.today()
    keys = paid_keys(session)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for item in session.exec(select(MiscSpending)).all():
        if not item.active:
            continue
        anchor = misc_due_anchor(item, today)
        amount = float(item.amount or 0)
        anchor_row = anchor_overdue_tuple(
            anchor,
            today,
            amount,
            is_paid=occurrence_is_paid(item.id, anchor, keys),
        )
        if anchor_row:
            due, amt = anchor_row
            out.append(
                {
                    "misc_spending_id": item.id,
                    "name": misc_display_name(item),
                    "amount": amt,
                    "due_date": due.isoformat(),
                    "days_overdue": (today - due).days,
                    "recurrence": item.recurrence or "monthly",
                    "kind": "misc",
                }
            )
            seen.add((item.id, due.isoformat()))

    out.sort(key=lambda x: (x["due_date"], x.get("name", "")))
    return out


def _advance_misc_anchor(item: MiscSpending, paid_due: date, paid_on: date) -> date | None:
    r = (item.recurrence or "monthly").lower()
    if not bill_repeats(r):
        item.recurrence = "none"
        item.due_date = None
        return None
    anchor = item.due_date or paid_due
    nxt = next_due_date_after_payment(anchor, r, paid_on)
    if nxt is not None:
        item.due_date = nxt
    return nxt


def mark_misc_paid(
    session: Session,
    item: MiscSpending,
    due_date: date,
    *,
    paid_date: date | None = None,
    amount: float | None = None,
) -> tuple[MiscOccurrence, date | None]:
    paid_on = paid_date or date.today()
    amt = float(amount if amount is not None else item.amount or 0)
    existing = session.exec(
        select(MiscOccurrence).where(
            MiscOccurrence.misc_spending_id == item.id,
            MiscOccurrence.due_date == due_date,
        )
    ).first()
    if existing:
        existing.paid = True
        existing.paid_date = paid_on
        existing.amount = amt
        inst = existing
    else:
        inst = MiscOccurrence(
            misc_spending_id=item.id,
            due_date=due_date,
            amount=amt,
            paid=True,
            paid_date=paid_on,
        )
    session.add(inst)
    next_due = _advance_misc_anchor(item, due_date, paid_on)
    item.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(item)
    session.commit()
    session.refresh(inst)
    session.refresh(item)
    from app.core.payment_archive_log import log_payment_marked

    log_payment_marked(
        session,
        action="marked_paid",
        kind="misc",
        name=item.name,
        amount=amt,
        scheduled_date=due_date,
        marked_date=paid_on,
        misc_spending_id=item.id,
    )
    return inst, next_due
