"""Per-occurrence bill payments via BillInstance; anchor due_date advances on pay."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from app.core.bill_deferral import bill_deferrals
from app.core.bill_schedule import (
    bill_repeats,
    expand_bill_occurrences,
    next_due_date_after_payment,
    scheduled_bill_payments,
)
from app.db.models import Bill, BillInstance


def paid_keys(session: Session) -> set[tuple[str, date]]:
    rows = session.exec(select(BillInstance).where(BillInstance.paid.is_(True))).all()
    return {(r.bill_id, r.due_date) for r in rows if r.due_date}


def _bill_id(bill: Bill | dict[str, Any]) -> str:
    return bill.id if isinstance(bill, Bill) else str(bill["id"])


def occurrence_is_paid(bill_id: str, due: date, keys: set[tuple[str, date]]) -> bool:
    return (bill_id, due) in keys


def resolved_bill_anchor(
    bill: Bill | dict[str, Any],
    keys: set[tuple[str, date]],
    as_of: date | None = None,
) -> date | None:
    """
    Next unpaid due anchor for scheduling.

    When bill.due_date points at an already-paid cycle (common after mark-paid then
    edit), advance to the next unpaid occurrence so cashflow and briefings stay correct.
    """
    today = as_of or date.today()
    if isinstance(bill, Bill):
        anchor = bill.due_date
        recurrence = bill.recurrence or "monthly"
        bid = bill.id
    else:
        anchor = bill.get("due_date")
        if isinstance(anchor, str):
            anchor = date.fromisoformat(anchor[:10])
        recurrence = bill.get("recurrence") or "monthly"
        bid = str(bill["id"])
    if not isinstance(anchor, date) or not bill_repeats(recurrence):
        return anchor if isinstance(anchor, date) else None
    cursor = anchor
    for _ in range(120):
        if not occurrence_is_paid(bid, cursor, keys):
            return cursor
        nxt = next_due_date_after_payment(cursor, recurrence, today)
        if nxt is None or nxt <= cursor:
            return cursor
        cursor = nxt
    return cursor


def sync_bill_due_anchor(session: Session, bill: Bill, keys: set[tuple[str, date]] | None = None) -> bool:
    """Persist bill.due_date when it still points at a paid cycle."""
    if bill.recurrence == "scheduled":
        keys = keys if keys is not None else paid_keys(session)
        remaining = [row for row in scheduled_bill_payments(bill) if (bill.id, row[0]) not in keys]
        next_due, next_amount = remaining[0] if remaining else (None, bill.amount)
        if bill.due_date == next_due and bill.amount == next_amount:
            return False
        bill.due_date = next_due
        bill.amount = next_amount
        bill.updated_at = datetime.now(UTC).replace(tzinfo=None)
        session.add(bill)
        return True
    if not bill.due_date or not bill_repeats(bill.recurrence or "monthly"):
        return False
    keys = keys if keys is not None else paid_keys(session)
    resolved = resolved_bill_anchor(bill, keys)
    if resolved is None or resolved == bill.due_date:
        return False
    bill.due_date = resolved
    bill.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(bill)
    return True


def _bill_for_schedule(
    bill: Bill | dict[str, Any],
    keys: set[tuple[str, date]],
    as_of: date,
) -> Bill | dict[str, Any]:
    if not isinstance(bill, Bill):
        return bill
    resolved = resolved_bill_anchor(bill, keys, as_of)
    if resolved is None or resolved == bill.due_date:
        return bill
    return {
        "id": bill.id,
        "name": bill.name,
        "amount": bill.amount,
        "due_date": resolved,
        "recurrence": bill.recurrence,
        "required": bill.required,
    }


def expand_bill_occurrences_unpaid(
    bill: Bill | dict[str, Any],
    start: date,
    end: date,
    keys: set[tuple[str, date]],
) -> list[tuple[date, float]]:
    bid = _bill_id(bill)
    scheduled = _bill_for_schedule(bill, keys, start)
    return [
        (due, amount)
        for due, amount in expand_bill_occurrences(scheduled, start, end)
        if not occurrence_is_paid(bid, due, keys)
    ]


def _overdue_anchor_amount(
    bill: Bill,
    period_start: date,
    keys: set[tuple[str, date]],
) -> float:
    if bill.recurrence == "scheduled":
        return sum(
            amount for due, amount in scheduled_bill_payments(bill)
            if due < period_start and (bill.id, due) not in keys
        )
    if not bill.required or not bill.due_date or bill.due_date >= period_start:
        return 0.0
    if occurrence_is_paid(bill.id, bill.due_date, keys):
        return 0.0
    return float(bill.amount)


def sum_bills_due_in_period_unpaid(
    session: Session,
    period_start: date,
    period_end: date,
) -> float:
    keys = paid_keys(session)
    deferrals = bill_deferrals(session)
    total = 0.0
    for bill in session.exec(select(Bill)).all():
        earliest = min((original for bid, original in deferrals if bid == bill.id), default=period_start)
        begin = min(period_start, earliest)
        for original, amount in expand_bill_occurrences_unpaid(bill, begin, period_end, keys):
            effective = deferrals.get((bill.id, original))
            payable = effective.deferred_due_date if effective else original
            if period_start <= payable <= period_end:
                total += amount
            elif payable < period_start and original == bill.due_date and bill.required:
                total += amount
    return round(total, 2)


def bill_events_in_range_unpaid(
    bills: list[Bill],
    start: date,
    end: date,
    keys: set[tuple[str, date]],
    session: Session | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    deferrals = bill_deferrals(session) if session is not None else {}
    for bill in bills:
        earliest = min((original for bid, original in deferrals if bid == bill.id), default=start)
        for due, amount in expand_bill_occurrences_unpaid(bill, min(start, earliest), end, keys):
            deferral = deferrals.get((bill.id, due))
            effective = deferral.deferred_due_date if deferral else due
            if not start <= effective <= end:
                continue
            events.append(
                {
                    "date": effective.isoformat(),
                    "type": "bill",
                    "name": bill.name,
                    "amount": amount,
                    "required": bill.required,
                    "recurrence": bill.recurrence,
                    "repeats": bill_repeats(bill.recurrence),
                    "bill_id": bill.id,
                    "due_date": due.isoformat(),
                    "deferred_once": deferral is not None,
                }
            )
    events.sort(key=lambda e: e["date"])
    return events


def _history_start(bill: Bill, as_of: date) -> date:
    created = bill.created_at.date() if bill.created_at else as_of
    return min(created, as_of - timedelta(days=400))


def unpaid_occurrences_for_bill(
    bill: Bill,
    keys: set[tuple[str, date]],
    *,
    through: date,
    start: date | None = None,
) -> list[tuple[date, float]]:
    begin = start if start is not None else _history_start(bill, through)
    if bill.recurrence == "scheduled" and start is None:
        payments = scheduled_bill_payments(bill)
        if payments:
            begin = min(begin, payments[0][0])
    return expand_bill_occurrences_unpaid(bill, begin, through, keys)


def _anchor_overdue(bill: Bill, today: date, keys: set[tuple[str, date]]) -> tuple[date, float] | None:
    if bill.recurrence == "scheduled":
        return None
    if not bill.required or not bill.due_date or bill.due_date >= today:
        return None
    if occurrence_is_paid(bill.id, bill.due_date, keys):
        return None
    return (bill.due_date, float(bill.amount))


def list_unpaid_past_due(session: Session, as_of: date | None = None) -> list[dict[str, Any]]:
    """Unpaid occurrences before today — driven by bill.due_date anchor and payment history."""
    today = as_of or date.today()
    keys = paid_keys(session)
    deferrals = bill_deferrals(session)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for bill in session.exec(select(Bill)).all():
        if not bill.required:
            continue

        anchor = _anchor_overdue(bill, today, keys)
        if anchor:
            due, amount = anchor
            deferral = deferrals.get((bill.id, due))
            effective = deferral.deferred_due_date if deferral else due
            if effective >= today:
                anchor = None
        if anchor:
            due, amount = anchor
            out.append(
                {
                    "bill_id": bill.id,
                    "name": bill.name,
                    "amount": amount,
                    "due_date": effective.isoformat(),
                    "days_overdue": (today - effective).days,
                    "recurrence": bill.recurrence,
                }
            )
            seen.add((bill.id, due.isoformat()))

        for due, amount in unpaid_occurrences_for_bill(
            bill, keys, through=today - timedelta(days=1)
        ):
            if due >= today:
                continue
            deferral = deferrals.get((bill.id, due))
            if deferral and deferral.deferred_due_date >= today:
                continue
            key = (bill.id, due.isoformat())
            if key in seen:
                continue
            out.append(
                {
                    "bill_id": bill.id,
                    "name": bill.name,
                    "amount": amount,
                    "due_date": (deferral.deferred_due_date if deferral else due).isoformat(),
                    "days_overdue": (today - (deferral.deferred_due_date if deferral else due)).days,
                    "recurrence": bill.recurrence,
                }
            )
            seen.add(key)

    out.sort(key=lambda x: (x["due_date"], x["name"]))
    return out


def pick_one_tap_occurrence(
    bill: Bill,
    keys: set[tuple[str, date]],
    as_of: date | None = None,
) -> tuple[date, float] | None:
    """Pay the bill's current due anchor, or the next unpaid occurrence in range."""
    today = as_of or date.today()
    if bill.recurrence == "scheduled":
        remaining = [row for row in scheduled_bill_payments(bill) if (bill.id, row[0]) not in keys]
        return remaining[0] if remaining and remaining[0][0] <= today + timedelta(days=60) else None
    anchor = _anchor_overdue(bill, today + timedelta(days=1), keys)
    if anchor:
        return anchor
    if bill.due_date and not occurrence_is_paid(bill.id, bill.due_date, keys):
        if bill.due_date <= today + timedelta(days=60):
            return (bill.due_date, float(bill.amount))
    future = expand_bill_occurrences_unpaid(bill, today, today + timedelta(days=60), keys)
    if future:
        return future[0]
    return None


def bill_has_unpaid_past_due(session: Session, bill: Bill, as_of: date | None = None) -> bool:
    today = as_of or date.today()
    return any(row["bill_id"] == bill.id for row in list_unpaid_past_due(session, today))


def _advance_bill_anchor(session: Session, bill: Bill, paid_due: date, paid_on: date) -> date | None:
    """Update bill.due_date to the next cycle after paid_due."""
    if bill.recurrence == "scheduled":
        keys = paid_keys(session) | {(bill.id, paid_due)}
        sync_bill_due_anchor(session, bill, keys)
        return bill.due_date
    r = (bill.recurrence or "monthly").lower()
    if not bill_repeats(r):
        bill.recurrence = "none"
        bill.due_date = None
        return None
    nxt = next_due_date_after_payment(paid_due, r, paid_on)
    if nxt is not None:
        bill.due_date = nxt
    return nxt


def mark_bill_paid(
    session: Session,
    bill: Bill,
    due_date: date,
    *,
    paid_date: date | None = None,
    amount: float | None = None,
) -> tuple[BillInstance, date | None]:
    # A timeline click may carry the approved effective date. Payment history
    # always records the original cycle key so the next recurrence stays fixed.
    for (bid, original), deferral in bill_deferrals(session, bill.id).items():
        if bid == bill.id and deferral.deferred_due_date == due_date:
            due_date = original
            break
    paid_on = paid_date or date.today()
    scheduled_amount = bill.amount
    if bill.recurrence == "scheduled":
        scheduled_amounts = dict(scheduled_bill_payments(bill))
        if due_date not in scheduled_amounts:
            raise ValueError("This date is not in the payment plan")
        scheduled_amount = scheduled_amounts[due_date]
    amt = float(amount if amount is not None else scheduled_amount)
    existing = session.exec(
        select(BillInstance).where(
            BillInstance.bill_id == bill.id,
            BillInstance.due_date == due_date,
        )
    ).first()
    if existing:
        existing.paid = True
        existing.paid_date = paid_on
        existing.amount = amt
        inst = existing
    else:
        inst = BillInstance(
            bill_id=bill.id,
            due_date=due_date,
            amount=amt,
            paid=True,
            paid_date=paid_on,
        )
    session.add(inst)

    next_due = _advance_bill_anchor(session, bill, due_date, paid_on)
    bill.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(bill)
    session.commit()
    session.refresh(inst)
    session.refresh(bill)
    from app.core.payment_archive_log import log_payment_marked

    log_payment_marked(
        session,
        action="marked_paid",
        kind="bill",
        name=bill.name,
        amount=amt,
        scheduled_date=due_date,
        marked_date=paid_on,
        bill_id=bill.id,
    )
    return inst, next_due


def unmark_bill_paid(session: Session, bill_id: str, due_date: date) -> bool:
    bill = session.get(Bill, bill_id)
    inst = session.exec(
        select(BillInstance).where(
            BillInstance.bill_id == bill_id,
            BillInstance.due_date == due_date,
        )
    ).first()
    if not inst:
        return False
    session.delete(inst)
    if bill and bill.recurrence == "scheduled":
        keys = paid_keys(session) - {(bill_id, due_date)}
        sync_bill_due_anchor(session, bill, keys)
    if bill and bill.due_date and bill_repeats(bill.recurrence or "monthly"):
        # Restore anchor to the paid cycle if we had advanced past it.
        if bill.due_date > due_date:
            bill.due_date = due_date
            bill.updated_at = datetime.now(UTC).replace(tzinfo=None)
            session.add(bill)
    session.commit()
    return True


def bill_payment_summary(session: Session, bill: Bill) -> dict[str, Any]:
    keys = paid_keys(session)
    deferrals = bill_deferrals(session, bill.id)
    if sync_bill_due_anchor(session, bill, keys):
        session.commit()
        session.refresh(bill)
    today = date.today()
    past_unpaid = [
        {
            "due_date": (deferrals[(bill.id, d)].deferred_due_date if (bill.id, d) in deferrals else d).isoformat(),
            "amount": a,
        }
        for d, a in unpaid_occurrences_for_bill(
            bill, keys, through=today - timedelta(days=1)
        )
        if (bill.id, d) not in deferrals or deferrals[(bill.id, d)].deferred_due_date < today
    ]
    one_tap = pick_one_tap_occurrence(bill, keys, today)
    next_due = bill.due_date.isoformat() if bill.due_date else None
    deferral = deferrals.get((bill.id, bill.due_date)) if bill.due_date else None
    effective_due = deferral.deferred_due_date.isoformat() if deferral else next_due
    return {
        "id": bill.id,
        "name": bill.name,
        "amount": bill.amount,
        "due_date": next_due,
        "next_due_date": effective_due,
        "approved_deferral": {
            "original_due_date": next_due,
            "deferred_due_date": effective_due,
            "approved_at": deferral.approved_at.isoformat(),
        } if deferral else None,
        "recurrence": bill.recurrence,
        "payments": [
            {"due_date": due.isoformat(), "amount": amount, "paid": (bill.id, due) in keys}
            for due, amount in scheduled_bill_payments(bill)
        ] if bill.recurrence == "scheduled" else [],
        "priority": bill.priority,
        "autopay": bill.autopay,
        "category": bill.category,
        "required": bill.required,
        "created_at": bill.created_at.isoformat() if bill.created_at else None,
        "updated_at": bill.updated_at.isoformat() if bill.updated_at else None,
        "unpaid_past_due": past_unpaid,
        "one_tap": (
            {"due_date": one_tap[0].isoformat(), "amount": one_tap[1]} if one_tap else None
        ),
    }
