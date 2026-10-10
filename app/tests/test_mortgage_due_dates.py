"""Mortgage due-by / late-by scheduling and AI context."""

from datetime import date, timedelta

from sqlmodel import select

from app.core.mortgage_optimizer import analyze_mortgage
from app.core.mortgage_schedule import (
    mortgage_due_by_anchor,
    mortgage_is_late,
    mortgage_schedule_meta,
    sum_mortgages_due_in_period,
)
from app.core.risk_engine import scan_risks
from app.db.models import MortgageAccount


def test_cashflow_uses_due_by_not_late(session):
    today = date.today()
    due_by = today + timedelta(days=5)
    late_by = today + timedelta(days=15)
    session.add(
        MortgageAccount(
            lender="Bank",
            original_balance=200_000,
            current_balance=180_000,
            interest_rate=6.5,
            monthly_payment=1200,
            escrow_amount=300,
            due_by_date=due_by,
            late_by_date=late_by,
            start_date=due_by,
        )
    )
    session.commit()

    total = sum_mortgages_due_in_period(session, today, today + timedelta(days=20))
    assert total == 1500

    meta = mortgage_schedule_meta(session.exec(select(MortgageAccount)).first())
    assert meta["due_by_date"] == due_by.isoformat()
    assert meta["late_by_date"] == late_by.isoformat()
    assert meta["grace_days_after_due_by"] == 10


def test_grace_period_not_late_until_after_late_by(session):
    today = date.today()
    due_by = today - timedelta(days=3)
    late_by = today + timedelta(days=5)
    m = MortgageAccount(
        lender="Grace",
        original_balance=100_000,
        current_balance=90_000,
        interest_rate=6.0,
        monthly_payment=800,
        due_by_date=due_by,
        late_by_date=late_by,
        recurrence="monthly",
    )
    assert mortgage_is_late(m, today) is False
    assert mortgage_due_by_anchor(m, today) == due_by

    session.add(m)
    session.commit()
    risks = scan_risks(session)
    assert any(r.get("code") == "mortgage_grace_period" for r in risks)
    assert not any(r.get("code") == "mortgage_late" for r in risks)


def test_analyze_mortgage_includes_payment_schedule(session):
    m = MortgageAccount(
        lender="AI Test",
        original_balance=100_000,
        current_balance=90_000,
        interest_rate=6.0,
        monthly_payment=800,
        due_by_date=date.today().replace(day=1),
        late_by_date=date.today().replace(day=10),
        recurrence="monthly",
    )
    session.add(m)
    session.commit()
    session.refresh(m)
    analysis = analyze_mortgage(m)
    assert "payment_schedule" in analysis
    assert analysis["payment_schedule"]["grace_days_after_due_by"] == 9
