"""Derive annual/household income from paycheck sources."""

from __future__ import annotations

from sqlmodel import Session, select

from app.db.models import BorrowerProfile, IncomeSource

PAY_PERIODS_PER_YEAR: dict[str, int] = {
    "weekly": 52,
    "biweekly": 26,
    "semimonthly": 24,
    "monthly": 12,
    "annual": 1,
    "yearly": 1,
}


def annual_income_from_sources(sources: list[IncomeSource] | None = None) -> float:
    """Sum annualized gross from all income sources."""
    total = 0.0
    for src in sources or []:
        amount = float(src.amount or 0)
        if amount <= 0:
            continue
        freq = (src.pay_frequency or "biweekly").lower().strip()
        periods = PAY_PERIODS_PER_YEAR.get(freq, 26)
        total += amount * periods
    return round(total, 2)


def gross_monthly_from_sources(sources: list[IncomeSource] | None = None) -> float:
    annual = annual_income_from_sources(sources)
    return round(annual / 12, 2) if annual > 0 else 0.0


def sync_borrower_income_from_sources(session: Session) -> BorrowerProfile | None:
    """Copy annualized income from IncomeSource rows into borrower profile when unset."""
    profile = session.exec(select(BorrowerProfile)).first()
    if profile is None:
        return None
    sources = list(session.exec(select(IncomeSource)).all())
    derived = annual_income_from_sources(sources)
    if derived <= 0:
        return profile
    if profile.annual_income > 0 or profile.household_income > 0:
        return profile
    profile.annual_income = derived
    profile.household_income = derived
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile
