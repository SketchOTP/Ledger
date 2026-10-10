"""Mortgage mark-paid and timeline tests."""

from datetime import date, timedelta

from sqlmodel import select

from app.core.liquidity_planner import _month_timeline
from app.core.mortgage_payment import mark_mortgage_paid, paid_keys
from app.core.obligation_schedule import obligation_api_row, upcoming_obligations
from app.db.models import MortgageAccount


def test_mark_mortgage_paid_excludes_from_schedule(session):
    today = date.today()
    due = today + timedelta(days=5)
    session.add(
        MortgageAccount(
            lender="Citizens Bank",
            original_balance=200_000,
            current_balance=180_000,
            interest_rate=6.5,
            monthly_payment=1942,
            due_by_date=due,
            recurrence="monthly",
        )
    )
    session.commit()
    mortgage = session.exec(select(MortgageAccount)).first()
    assert mortgage is not None
    before = len(upcoming_obligations(session, days=30))
    mark_mortgage_paid(session, mortgage, due)
    after = len(upcoming_obligations(session, days=30))
    assert after < before
    assert paid_keys(session)


def test_month_timeline_sorts_income_before_outflow_same_day():
    steps = [
        {"date": "2026-06-03", "action": "pay_from_checking", "name": "Bill"},
        {"date": "2026-06-03", "action": "deposit_to_checking", "name": "Pay"},
        {"date": "2026-06-05", "action": "pay_from_checking", "name": "Mortgage"},
    ]
    timeline = _month_timeline(steps)
    assert timeline[0]["step_kind"] == "in"
    assert timeline[1]["name"] == "Bill"


def test_month_timeline_preserves_income_checking_balance():
    steps = [
        {
            "date": "2026-06-03",
            "action": "deposit_to_checking",
            "name": "Paycheck",
            "amount": 2000,
            "checking_balance": 2890,
            "balance_after": 2890,
        },
        {
            "date": "2026-06-04",
            "action": "pay_from_checking",
            "name": "Rent",
            "amount": 1500,
            "checking_balance": 1390,
            "balance_after": 1390,
        },
    ]
    timeline = _month_timeline(steps)
    assert timeline[0]["step_kind"] == "in"
    assert timeline[0]["checking_balance"] == 2890
    assert timeline[1]["checking_balance"] == 1390


def test_obligation_api_row_mortgage_id(session):
    today = date.today()
    session.add(
        MortgageAccount(
            lender="Bank",
            original_balance=100_000,
            current_balance=90_000,
            interest_rate=5.0,
            monthly_payment=800,
            due_by_date=today + timedelta(days=4),
            recurrence="monthly",
        )
    )
    session.commit()
    row = upcoming_obligations(session, days=14)[0]
    api_row = obligation_api_row(row)
    assert api_row["can_mark_paid"] is True
    assert api_row["mortgage_id"]
