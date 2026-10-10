"""Per-occurrence rent payments via RentOccurrence (borrower profile)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlmodel import Session, select

from app.core.bill_schedule import next_due_date_after_payment
from app.core.rent_schedule import expand_rent_occurrences, is_rent_housing, rent_due_anchor
from app.core.schedule_overdue import anchor_overdue_tuple, overdue_anchor_amount
from app.db.models import BorrowerProfile, RentOccurrence


def paid_keys(session: Session) -> set[tuple[str, date]]:
    rows = session.exec(select(RentOccurrence).where(RentOccurrence.paid.is_(True))).all()
    return {(r.profile_id, r.due_date) for r in rows if r.due_date}


def occurrence_is_paid(profile_id: str, due: date, keys: set[tuple[str, date]]) -> bool:
    return (profile_id, due) in keys


def _overdue_anchor_amount(
    profile: BorrowerProfile,
    period_start: date,
    keys: set[tuple[str, date]],
) -> float:
    anchor = rent_due_anchor(profile, period_start)
    paid = occurrence_is_paid(profile.id, anchor, keys)
    amount = float(profile.monthly_rent or 0)
    return overdue_anchor_amount(anchor, period_start, amount, is_paid=paid)


def sum_rent_due_in_period_unpaid(
    session: Session,
    period_start: date,
    period_end: date,
) -> float:
    profile = session.exec(select(BorrowerProfile)).first()
    if not profile or not is_rent_housing(session):
        return 0.0
    keys = paid_keys(session)
    anchor = rent_due_anchor(profile, period_start)
    anchor_amt = _overdue_anchor_amount(profile, period_start, keys)
    total = anchor_amt
    for due, amount in expand_rent_occurrences(profile, period_start, period_end):
        if occurrence_is_paid(profile.id, due, keys):
            continue
        if due == anchor and anchor_amt > 0:
            continue
        total += amount
    return round(total, 2)


def rent_events_in_range_unpaid(
    session: Session,
    start: date,
    end: date,
    keys: set[tuple[str, date]],
) -> list[dict[str, Any]]:
    profile = session.exec(select(BorrowerProfile)).first()
    if not profile or not is_rent_housing(session):
        return []
    anchor = rent_due_anchor(profile, start)
    anchor_amt = _overdue_anchor_amount(profile, start, keys)
    events: list[dict[str, Any]] = []
    if anchor_amt > 0:
        events.append(
            {
                "date": anchor.isoformat(),
                "type": "rent",
                "name": "Rent",
                "amount": anchor_amt,
                "profile_id": profile.id,
                "due_date": anchor.isoformat(),
            }
        )
    for due, amount in expand_rent_occurrences(profile, start, end):
        if occurrence_is_paid(profile.id, due, keys):
            continue
        if due == anchor and anchor_amt > 0:
            continue
        events.append(
            {
                "date": due.isoformat(),
                "type": "rent",
                "name": "Rent",
                "amount": amount,
                "profile_id": profile.id,
                "due_date": due.isoformat(),
            }
        )
    return events


def list_unpaid_rent_past_due(session: Session, as_of: date | None = None) -> list[dict[str, Any]]:
    today = as_of or date.today()
    profile = session.exec(select(BorrowerProfile)).first()
    if not profile or not is_rent_housing(session):
        return []
    keys = paid_keys(session)
    anchor = rent_due_anchor(profile, today)
    amount = float(profile.monthly_rent or 0)
    anchor_row = anchor_overdue_tuple(
        anchor,
        today,
        amount,
        is_paid=occurrence_is_paid(profile.id, anchor, keys),
    )
    if not anchor_row:
        return []
    due, amt = anchor_row
    return [
        {
            "profile_id": profile.id,
            "name": "Rent",
            "amount": amt,
            "due_date": due.isoformat(),
            "days_overdue": (today - due).days,
            "recurrence": "monthly",
            "kind": "rent",
        }
    ]


def mark_rent_paid(
    session: Session,
    profile: BorrowerProfile,
    due_date: date,
    *,
    paid_date: date | None = None,
    amount: float | None = None,
) -> tuple[RentOccurrence, date | None]:
    paid_on = paid_date or date.today()
    amt = float(amount if amount is not None else profile.monthly_rent or 0)
    existing = session.exec(
        select(RentOccurrence).where(
            RentOccurrence.profile_id == profile.id,
            RentOccurrence.due_date == due_date,
        )
    ).first()
    if existing:
        existing.paid = True
        existing.paid_date = paid_on
        existing.amount = amt
        inst = existing
    else:
        inst = RentOccurrence(
            profile_id=profile.id,
            due_date=due_date,
            amount=amt,
            paid=True,
            paid_date=paid_on,
        )
    session.add(inst)

    anchor = profile.rent_due_date or due_date
    nxt = next_due_date_after_payment(anchor, "monthly", paid_on)
    if nxt is not None:
        profile.rent_due_date = nxt
    profile.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(profile)
    session.commit()
    session.refresh(inst)
    session.refresh(profile)
    from app.core.payment_archive_log import log_payment_marked

    log_payment_marked(
        session,
        action="marked_paid",
        kind="rent",
        name="Rent",
        amount=amt,
        scheduled_date=due_date,
        marked_date=paid_on,
        profile_id=profile.id,
    )
    return inst, nxt
