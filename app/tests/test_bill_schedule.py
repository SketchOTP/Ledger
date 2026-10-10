"""Recurring bill scheduling tests."""

from datetime import date, timedelta

from sqlmodel import select

from app.core.bill_schedule import expand_bill_occurrences, sum_bills_due_in_period
from app.core.cashflow_engine import calculate_cashflow
from app.db.models import Bill, IncomeSource


def test_monthly_bill_repeats_in_pay_period(session):
    today = date.today()
    # Rent due on 1st — next occurrence must fall inside the pay window
    due = today.replace(day=1)
    if due < today:
        month = today.month % 12 + 1
        year = today.year + (1 if today.month == 12 else 0)
        due = date(year, month, 1)
    pay_period_end = today + timedelta(days=14)
    if due > pay_period_end:
        pay_period_end = due
    session.add(
        IncomeSource(
            source_name="Job",
            amount=2000,
            next_pay_date=pay_period_end,
        )
    )
    session.add(
        Bill(
            name="Rent",
            amount=1200,
            due_date=due,
            recurrence="monthly",
            required=True,
        )
    )
    session.commit()
    total = sum_bills_due_in_period(session, today, pay_period_end)
    assert total >= 1200
    cf = calculate_cashflow(session, starting_cash=500)
    assert cf["required_bills"] >= 1200


def test_once_bill_not_repeated(session):
    today = date.today()
    session.add(
        Bill(
            name="One-off",
            amount=100,
            due_date=today + timedelta(days=5),
            recurrence="once",
            required=True,
        )
    )
    session.commit()
    total = sum_bills_due_in_period(session, today, today + timedelta(days=60))
    assert total == 100
    bill = session.exec(select(Bill)).first()
    occ = expand_bill_occurrences(bill, today, today + timedelta(days=60))
    assert len(occ) == 1
