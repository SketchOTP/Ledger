"""Mortgage payment scheduling in cashflow."""

from datetime import date, timedelta

from app.core.cashflow_engine import calculate_cashflow
from app.core.cashflow_plan import build_cashflow_plan
from app.core.monthly_obligations import monthly_obligations_breakdown
from app.core.mortgage_schedule import monthly_mortgage_payments_total, sum_mortgages_due_in_period
from app.db.models import Bill, Debt, IncomeSource, MortgageAccount


def test_mortgage_in_monthly_obligations_not_bills(session):
    session.add(
        Bill(name="Electric", amount=100, recurrence="monthly", required=True, due_date=date.today())
    )
    session.add(
        Debt(creditor="Card", balance=1000, apr=20, minimum_payment=50, due_date=date.today())
    )
    session.add(
        MortgageAccount(
            lender="Bank",
            original_balance=200_000,
            current_balance=180_000,
            interest_rate=6.5,
            monthly_payment=1200,
            escrow_amount=300,
            start_date=date.today().replace(day=1),
        )
    )
    session.commit()

    ob = monthly_obligations_breakdown(session)
    assert ob["bills"] == 100
    assert ob["debt_minimums"] == 50
    assert ob["mortgage_payments"] == 1500
    assert ob["total"] == 1650


def test_mortgage_due_in_pay_period_and_plan(session):
    today = date.today()
    # Keep exactly one occurrence in this 14-day window in every month.
    due = today + timedelta(days=1)
    session.add(
        IncomeSource(
            source_name="Job",
            amount=4000,
            next_pay_date=today + timedelta(days=14),
        )
    )
    session.add(
        MortgageAccount(
            lender="Wells",
            original_balance=250_000,
            current_balance=200_000,
            interest_rate=7.0,
            monthly_payment=1800,
            escrow_amount=200,
            start_date=due,
        )
    )
    session.commit()

    period_end = today + timedelta(days=14)
    assert sum_mortgages_due_in_period(session, today, period_end) == 2000
    assert monthly_mortgage_payments_total(session) == 2000

    cf = calculate_cashflow(session, starting_cash=5000)
    assert cf["mortgage_due_this_period"] == 2000
    assert cf["monthly_obligations_total"] == 2000

    plan = build_cashflow_plan(session, horizon_days=60)
    mortgage_events = [e for e in plan["upcoming_events"] if e["type"] == "mortgage"]
    assert len(mortgage_events) >= 1
    assert any(e["amount"] == 2000 for e in mortgage_events)


def test_debt_recurrence_off_excluded_from_monthly_total(session):
    session.add(
        Debt(
            creditor="Paused",
            balance=500,
            apr=10,
            minimum_payment=100,
            due_date=date.today(),
            recurrence="none",
        )
    )
    session.add(
        Debt(
            creditor="Active",
            balance=1000,
            apr=15,
            minimum_payment=50,
            due_date=date.today(),
            recurrence="monthly",
        )
    )
    session.commit()
    ob = monthly_obligations_breakdown(session)
    assert ob["debt_minimums"] == 50


def test_mortgage_debt_type_skipped_when_mortgage_account_exists(session):
    session.add(
        MortgageAccount(
            lender="Bank",
            original_balance=100_000,
            current_balance=90_000,
            interest_rate=6.0,
            monthly_payment=900,
            start_date=date.today().replace(day=1),
        )
    )
    session.add(
        Debt(
            creditor="Old lender",
            balance=90_000,
            apr=6,
            minimum_payment=900,
            debt_type="mortgage",
            due_date=date.today(),
        )
    )
    session.commit()

    ob = monthly_obligations_breakdown(session)
    assert ob["mortgage_payments"] == 900
    assert ob["debt_minimums"] == 0
    assert ob["total"] == 900
