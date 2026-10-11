"""Paid purchases leave future cash needs; checking remains owner-entered."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_db
from app.core.budget_engine import planned_spending_daily_events, spending_attention
from app.core.liquidity_planner import build_liquidity_plan
from app.db.models import BorrowerProfile, BudgetCategory, Transaction
from app.main import app


@pytest.fixture
def paid_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(BorrowerProfile(checking_balance=1000, target_buffer=0, cash_cushion_balance=0))
        session.commit()
        app.dependency_overrides[get_db] = lambda: session
        try:
            yield TestClient(app), session
        finally:
            app.dependency_overrides.clear()
    engine.dispose()


@pytest.mark.parametrize("kind,amount", [("day", 30), ("week", 150)])
def test_mark_paid_uses_entered_checking_without_reserving_purchase_again(paid_client, kind, amount):
    client, session = paid_client
    today = date.today()
    category = BudgetCategory(name="Groceries" if kind == "week" else "Daily Needs",
                              monthly_limit=1000, usual_amount=amount, period=kind, planned_weekday=today.weekday())
    session.add(category)
    session.commit()
    payload = {"budget_category_id": category.id, "planned_date": today.isoformat(),
               "transaction_date": today.isoformat(), "amount": amount}
    before = build_liquidity_plan(session, as_of=today)["money_state"]["payment_plan"]
    response = client.post("/api/spending/record-planned", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["reflected_in_checking"] is True
    assert session.exec(select(BorrowerProfile)).one().checking_balance == 1000
    after = build_liquidity_plan(session, as_of=today)["money_state"]["payment_plan"]
    assert after["opening_checking"] == before["opening_checking"] == 1000
    assert after["projected_ending_checking"] == pytest.approx(before["projected_ending_checking"] + amount)
    assert not any(row.get("spending_kind") and row["date"] == today.isoformat() for row in after["rows"])
    assert client.get("/api/spending/pending-checking").json() == {"count": 0}
    assert client.get("/api/v2/today").json()["spending_attention"]["pending_checking_count"] == 0
    if kind == "week":
        assert client.post("/api/spending/record-planned", json=payload).status_code == 409
    update = client.post("/api/profile/borrower", json={"checking_balance": 850})
    assert update.status_code == 200, update.text
    updated = build_liquidity_plan(session, as_of=today)["money_state"]["payment_plan"]
    assert updated["opening_checking"] == 850
    assert updated["projected_ending_checking"] == pytest.approx(after["projected_ending_checking"] - 150)


@pytest.mark.parametrize("days_later", [0, 1, 7])
def test_preexisting_paid_marks_never_return_as_pending(session, days_later):
    day = date(2026, 10, 10)
    daily = BudgetCategory(name="Daily Needs", monthly_limit=1000, usual_amount=30, period="day")
    groceries = BudgetCategory(name="Groceries", monthly_limit=650, usual_amount=150, period="week", planned_weekday=5)
    session.add_all([daily, groceries])
    session.commit()
    for category, amount in [(daily, 30), (groceries, 150)]:
        session.add(Transaction(category=category.name, amount=amount, transaction_date=day,
                                planned_budget_category_id=category.id, planned_spending_date=day,
                                reflected_in_checking=False))
    session.commit()
    as_of = day + timedelta(days=days_later)
    attention = spending_attention(session, as_of)
    assert attention["pending_checking_count"] == 0
    assert not any(row["awaiting_checking_update"] for row in attention["everyday"] + attention["grocery_trips"])
    rows = planned_spending_daily_events(session, as_of, as_of + timedelta(days=7))
    assert not any(row.get("awaiting_checking_update") for row in rows)
    assert not any(row["spending_kind"] == "recorded_pending" for row in rows)
    if not days_later:
        assert sum(row["amount"] for row in rows if row["date"] == day.isoformat()) == 0
    else:
        assert next(row for row in rows if row["spending_kind"] == "daily")["amount"] == 30
    assert any(row["spending_kind"] == "groceries" and row["amount"] == 150 for row in rows)
