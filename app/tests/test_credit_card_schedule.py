"""Credit card minimum payment scheduling tests."""

from datetime import date, timedelta

from app.core.credit_card_schedule import (
    cards_for_cashflow,
    monthly_credit_card_minimums_total,
    sum_cards_due_in_period,
)
from app.core.monthly_obligations import monthly_obligations_breakdown
from app.db.models import CreditCard


def test_card_minimums_in_monthly_obligations(session):
    today = date.today()
    session.add(
        CreditCard(
            issuer="Chase",
            card_name="Sapphire",
            balance=2000,
            credit_limit=5000,
            apr=22,
            minimum_payment=75,
            due_date=today + timedelta(days=5),
            recurrence="monthly",
        )
    )
    session.commit()
    ob = monthly_obligations_breakdown(session)
    assert ob["credit_card_minimums"] == 75
    assert ob["total"] >= 75


def test_paid_off_card_not_scheduled(session):
    session.add(
        CreditCard(
            issuer="Paid",
            card_name="Off",
            balance=0,
            credit_limit=5000,
            apr=18,
            minimum_payment=25,
            recurrence="monthly",
        )
    )
    session.commit()
    assert cards_for_cashflow(session) == []
    assert monthly_credit_card_minimums_total(session) == 0


def test_card_due_before_pay_period(session):
    today = date.today()
    session.add(
        CreditCard(
            issuer="Amex",
            card_name="Gold",
            balance=500,
            credit_limit=3000,
            apr=24,
            minimum_payment=50,
            due_date=today + timedelta(days=3),
            recurrence="monthly",
        )
    )
    session.commit()
    assert sum_cards_due_in_period(session, today, today + timedelta(days=14)) == 50
