"""Income aggregation from paycheck sources."""

from sqlmodel import select

from app.core.income_aggregate import annual_income_from_sources, sync_borrower_income_from_sources
from app.db.models import BorrowerProfile, IncomeSource


def test_annual_income_biweekly(session):
    session.add(IncomeSource(source_name="Job", amount=2000, pay_frequency="biweekly"))
    session.commit()
    sources = list(session.exec(select(IncomeSource)).all())
    assert annual_income_from_sources(sources) == 52000.0


def test_sync_borrower_income(session):
    session.add(BorrowerProfile(checking_balance=50))
    session.add(IncomeSource(source_name="Job", amount=1000, pay_frequency="monthly"))
    session.commit()
    sync_borrower_income_from_sources(session)
    profile = session.exec(select(BorrowerProfile)).first()
    assert profile.household_income == 12000.0
    assert profile.annual_income == 12000.0
