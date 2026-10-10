"""Obligation schedule aggregates all payment types."""

from datetime import date, timedelta

from app.core.obligation_schedule import obligation_api_row, upcoming_obligations
from app.db.models import Bill, MortgageAccount


def test_upcoming_obligations_include_mortgage_and_bills(session):
    today = date.today()
    due = today + timedelta(days=5)
    session.add(Bill(name="Electric", amount=120, required=True, due_date=due, recurrence="monthly"))
    session.add(
        MortgageAccount(
            lender="Bank",
            original_balance=200_000,
            current_balance=180_000,
            interest_rate=6.5,
            monthly_payment=1500,
            due_by_date=date(today.year, today.month, 5) if today.day <= 5 else due,
            recurrence="monthly",
        )
    )
    session.commit()

    events = upcoming_obligations(session, days=30)
    kinds = {e.get("kind") for e in events}
    names = {e.get("name") for e in events}
    assert "bill" in kinds
    assert "mortgage" in kinds
    assert "Electric" in names


def test_obligation_api_row_exposes_mark_paid_ids(session):
    today = date.today()
    session.add(Bill(name="Gas", amount=45, required=True, due_date=today + timedelta(days=2)))
    session.commit()
    row = upcoming_obligations(session, days=7)[0]
    api_row = obligation_api_row(row)
    assert api_row["can_mark_paid"] is True
    assert api_row["bill_id"]
    assert api_row["due_date"]


def test_next_bills_briefing_helper_uses_30_day_obligations(session):
    today = date.today()
    session.add(Bill(name="Phone", amount=80, required=True, due_date=today + timedelta(days=3)))
    session.commit()
    rows = upcoming_obligations(session, days=30)
    assert any(r.get("name") == "Phone" for r in rows)
