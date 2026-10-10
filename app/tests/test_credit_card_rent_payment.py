"""Credit card and rent mark-paid tests."""

from datetime import date, timedelta

from sqlmodel import select

from app.core.credit_card_payment import mark_credit_card_paid
from app.core.credit_card_payment import paid_keys as card_paid_keys
from app.core.liquidity_planner import build_cfo_payment_plan
from app.core.obligation_schedule import obligation_api_row, upcoming_obligations
from app.core.rent_payment import mark_rent_paid
from app.core.rent_payment import paid_keys as rent_paid_keys
from app.db.models import BorrowerProfile, CreditCard


def test_mark_credit_card_paid_excludes_from_timeline(session):
    today = date.today()
    due = today + timedelta(days=4)
    session.add(
        CreditCard(
            issuer="Chase",
            card_name="Freedom",
            balance=500,
            credit_limit=5000,
            apr=24.9,
            minimum_payment=25,
            due_date=due,
            recurrence="monthly",
        )
    )
    session.commit()
    card = session.exec(select(CreditCard)).first()
    assert card is not None
    mark_credit_card_paid(session, card, due)
    assert (card.id, due) in card_paid_keys(session)
    plan = build_cfo_payment_plan(session, days=14)
    outs = [s for s in plan.get("month_timeline", []) if s.get("credit_card_id") == card.id]
    assert not any((s.get("due_date") or s.get("date") or "")[:10] == due.isoformat() for s in outs)


def test_mark_rent_paid_advances_due_date(session):
    today = date.today()
    due = today + timedelta(days=6)
    profile = BorrowerProfile(
        housing_status="rent",
        monthly_rent=1800,
        rent_due_date=due,
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    mark_rent_paid(session, profile, due)
    session.refresh(profile)
    assert profile.rent_due_date is not None
    assert profile.rent_due_date > due
    assert (profile.id, due) in rent_paid_keys(session)
    rows = [r for r in upcoming_obligations(session, days=14) if r.get("kind") == "rent"]
    assert not any((r.get("due_date") or "")[:10] == due.isoformat() for r in rows)


def test_obligation_api_row_credit_card_id(session):
    api_row = obligation_api_row(
        {
            "name": "Chase min",
            "amount": 25,
            "due_date": "2026-06-01",
            "kind": "credit_card",
            "provenance": {"credit_card_id": "card-1"},
        }
    )
    assert api_row["can_mark_paid"] is True
    assert api_row["credit_card_id"] == "card-1"
