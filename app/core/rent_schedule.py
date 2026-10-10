"""Schedule rent payments from borrower profile when housing_status is rent."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlmodel import Session, select

from app.core.bill_schedule import dates_in_range
from app.db.models import BorrowerProfile


def _profile(session: Session) -> BorrowerProfile | None:
    return session.exec(select(BorrowerProfile)).first()


def is_rent_housing(session: Session) -> bool:
    profile = _profile(session)
    return bool(profile and (profile.housing_status or "rent").lower() == "rent")


def is_mortgage_housing(session: Session) -> bool:
    profile = _profile(session)
    return bool(profile and (profile.housing_status or "").lower() == "mortgage")


def rent_due_anchor(profile: BorrowerProfile, range_start: date) -> date:
    anchor = profile.rent_due_date
    if isinstance(anchor, date):
        return anchor
    return range_start.replace(day=1)


def expand_rent_occurrences(profile: BorrowerProfile, start: date, end: date) -> list[tuple[date, float]]:
    amount = float(profile.monthly_rent or 0)
    if amount <= 0:
        return []
    anchor = rent_due_anchor(profile, start)
    return [(d, amount) for d in dates_in_range(start, end, anchor, "monthly")]


def sum_rent_due_in_period(session: Session, period_start: date, period_end: date) -> float:
    from app.core.rent_payment import sum_rent_due_in_period_unpaid

    return sum_rent_due_in_period_unpaid(session, period_start, period_end)


def rent_events_in_range(session: Session, start: date, end: date) -> list[dict[str, Any]]:
    profile = _profile(session)
    if not profile or not is_rent_housing(session):
        return []
    events: list[dict[str, Any]] = []
    for due, amount in expand_rent_occurrences(profile, start, end):
        events.append(
            {
                "date": due.isoformat(),
                "type": "rent",
                "name": "Rent",
                "amount": amount,
            }
        )
    return events


def monthly_rent_total(session: Session) -> float:
    profile = _profile(session)
    if not profile or not is_rent_housing(session):
        return 0.0
    return round(float(profile.monthly_rent or 0), 2)
