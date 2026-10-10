"""Housing outflows: rent (borrower profile) or mortgage accounts."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlmodel import Session, select

from app.core.mortgage_schedule import (
    monthly_mortgage_payments_total,
    sum_mortgages_due_in_period,
)
from app.core.rent_schedule import (
    is_mortgage_housing,
    is_rent_housing,
    monthly_rent_total,
    sum_rent_due_in_period,
)
from app.db.models import MortgageAccount


def _uses_rent_schedule(session: Session) -> bool:
    """Rent schedule only when explicitly rent and no mortgage accounts on file."""
    if is_mortgage_housing(session):
        return False
    if session.exec(select(MortgageAccount)).first():
        return False
    return is_rent_housing(session)


def monthly_housing_payments_total(session: Session) -> float:
    if _uses_rent_schedule(session):
        return monthly_rent_total(session)
    return monthly_mortgage_payments_total(session)


def sum_housing_due_in_period(session: Session, period_start: date, period_end: date) -> float:
    if _uses_rent_schedule(session):
        return sum_rent_due_in_period(session, period_start, period_end)
    return sum_mortgages_due_in_period(session, period_start, period_end)


def housing_events_in_range(session: Session, start: date, end: date) -> list[dict[str, Any]]:
    if _uses_rent_schedule(session):
        from app.core.rent_payment import paid_keys as rent_paid_keys
        from app.core.rent_payment import rent_events_in_range_unpaid

        return rent_events_in_range_unpaid(session, start, end, rent_paid_keys(session))
    from app.core.mortgage_payment import mortgage_events_in_range_unpaid, paid_keys

    mortgages = list(session.exec(select(MortgageAccount)).all())
    return mortgage_events_in_range_unpaid(mortgages, start, end, paid_keys(session), session)
