"""Debt occurrence payment tests."""

from datetime import date, timedelta

from sqlmodel import Session

from app.core.debt_payment import (
    list_unpaid_debt_past_due,
    mark_debt_paid,
    paid_keys,
    pick_one_tap_occurrence,
)
from app.core.debt_schedule import sum_debts_due_in_period
from app.db.models import Debt


def test_overdue_debt_in_period_sum(session: Session):
    today = date.today()
    debt = Debt(
        creditor="53rd Bank",
        balance=1000,
        apr=17,
        minimum_payment=255,
        due_date=today - timedelta(days=1),
        recurrence="monthly",
    )
    session.add(debt)
    session.commit()
    total = sum_debts_due_in_period(session, today, today + timedelta(days=14))
    assert total >= 255


def test_mark_debt_paid_advances_due_date(session: Session):
    today = date.today()
    due = today - timedelta(days=1)
    debt = Debt(
        creditor="Card",
        balance=500,
        apr=20,
        minimum_payment=50,
        due_date=due,
        recurrence="monthly",
    )
    session.add(debt)
    session.commit()
    session.refresh(debt)

    mark_debt_paid(session, debt, due)
    session.refresh(debt)
    assert debt.due_date is not None
    assert debt.due_date > due
    assert not list_unpaid_debt_past_due(session)


def test_pick_one_tap_prefers_overdue(session: Session):
    today = date.today()
    due = today - timedelta(days=2)
    debt = Debt(
        creditor="Bank",
        balance=1000,
        apr=10,
        minimum_payment=100,
        due_date=due,
        recurrence="monthly",
    )
    session.add(debt)
    session.commit()
    session.refresh(debt)
    pick = pick_one_tap_occurrence(debt, paid_keys(session))
    assert pick is not None
    assert pick[0] == due
