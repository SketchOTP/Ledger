"""Debt payment scheduling tests."""

from datetime import date, timedelta

from app.core.cashflow_engine import calculate_cashflow
from app.core.debt_schedule import sum_debts_due_in_period
from app.db.models import Debt, IncomeSource


def test_debt_due_in_pay_period(session):
    today = date.today()
    due = today + timedelta(days=5)
    session.add(
        IncomeSource(
            source_name="Job",
            amount=3000,
            next_pay_date=today + timedelta(days=14),
        )
    )
    session.add(
        Debt(
            creditor="Card",
            balance=2000,
            apr=20,
            minimum_payment=150,
            due_date=due,
        )
    )
    session.commit()
    period_end = today + timedelta(days=14)
    assert sum_debts_due_in_period(session, today, period_end) == 150
    cf = calculate_cashflow(session, starting_cash=1000)
    assert cf["debt_due_this_period"] == 150
    assert cf["monthly_debt_minimums"] == 150
