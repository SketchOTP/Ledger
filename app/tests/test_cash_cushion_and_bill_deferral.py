"""Cash-cushion truth and one-time, approved bill-date behavior."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_db
from app.core.bill_deferral import record_approved_bill_deferral
from app.core.bill_payment import bill_payment_summary, list_unpaid_past_due, mark_bill_paid, paid_keys
from app.core.bill_schedule import advance_anchor, bill_events_in_range
from app.core.liquidity_planner import build_liquidity_plan
from app.db.models import Bill, BillDeferral, BorrowerProfile, BudgetCategory, IncomeSource
from app.main import app


def test_configured_goal_is_not_inflated_by_an_automatic_half_goal(session):
    today = date.today()
    session.add(BorrowerProfile(checking_balance=3_000, target_buffer=1_000))
    session.commit()
    state = build_liquidity_plan(session, as_of=today)["money_state"]
    assert state["cash_cushion_goal"] == 1_000
    assert state["cash_cushion_current"] is None
    assert state["cash_cushion_refill"] == 0
    assert state["extra_safety_holdback"] == 0
    assert state["safety_cushion"] == 1_000


def test_known_cushion_is_earmarked_and_refills_in_small_safe_steps(session):
    profile = BorrowerProfile(checking_balance=2_000, target_buffer=1_000, cash_cushion_balance=200)
    session.add(profile)
    session.add(BudgetCategory(name="Groceries", monthly_limit=300))
    session.commit()
    state = build_liquidity_plan(session)["money_state"]
    assert state["cash_cushion_current"] == 200
    assert state["cash_cushion_gap"] == 800
    assert state["cash_cushion_refill"] == 0  # The configured goal is already protected.
    assert state["cash_cushion_refill"] <= 0.10 * state["discretionary_before_refill"] + 0.01
    assert state["protected_cash_breakdown"]["total"] == state["protected_cash"]
    assert state["liquid_resources"] == 2_000  # cushion is not added to account cash
    profile.cash_cushion_balance = 1_000
    session.add(profile)
    session.commit()
    full = build_liquidity_plan(session)["money_state"]
    assert full["cash_cushion_refill"] == 0
    profile.cash_cushion_balance = 100  # owner reports using the cushion
    session.add(profile)
    session.commit()
    dipped = build_liquidity_plan(session)["money_state"]
    assert dipped["cash_cushion_gap"] == 900
    assert dipped["cash_cushion_refill"] == 0
    assert dipped["working_safety_holdback"] == 1_000


def test_refill_does_not_take_money_needed_for_bill(session):
    today = date.today()
    session.add(BorrowerProfile(checking_balance=200, target_buffer=1_000, cash_cushion_balance=0))
    session.add(Bill(name="Required", amount=190, due_date=today + timedelta(days=2)))
    session.commit()
    state = build_liquidity_plan(session)["money_state"]
    assert state["cash_cushion_refill"] <= 1
    assert state["deployable_surplus"] <= 10


def test_approved_one_time_deferral_moves_only_one_occurrence(session):
    today = date.today()
    original = today + timedelta(days=2)
    deferred = today + timedelta(days=5)
    bill = Bill(name="Internet", amount=100, due_date=original, recurrence="monthly")
    session.add(bill)
    session.add(BorrowerProfile(checking_balance=50, target_buffer=0))
    session.add(IncomeSource(source_name="Pay", amount=200, next_pay_date=today + timedelta(days=4)))
    session.commit()
    before = build_liquidity_plan(session)["money_state"]["cash_rescue"]
    assert before["first_gap"] is not None
    with pytest.raises(ValueError, match="payee approves"):
        record_approved_bill_deferral(
            session, bill, original_due_date=original, deferred_due_date=deferred,
            payee_approved=False,
        )
    record_approved_bill_deferral(
        session, bill, original_due_date=original, deferred_due_date=deferred,
        payee_approved=True,
    )
    with pytest.raises(ValueError, match="already deferred"):
        record_approved_bill_deferral(
            session, bill, original_due_date=original, deferred_due_date=deferred + timedelta(days=1),
            payee_approved=True,
        )
    assert bill.due_date == original
    events = bill_events_in_range([bill], today, advance_anchor(original, "monthly"), session)
    assert [(e["date"], e["due_date"]) for e in events] == [
        (deferred.isoformat(), original.isoformat()),
        (advance_anchor(original, "monthly").isoformat(),) * 2,
    ]
    after = build_liquidity_plan(session)["money_state"]["cash_rescue"]
    assert after["first_gap"] is None
    assert bill_payment_summary(session, bill)["approved_deferral"]["deferred_due_date"] == deferred.isoformat()
    assert session.exec(select(BillDeferral)).first() is not None
    mark_bill_paid(session, bill, deferred)
    assert (bill.id, original) in paid_keys(session)
    assert bill.due_date == advance_anchor(original, "monthly")


def test_approved_date_suppresses_original_overdue_but_not_new_overdue(session):
    today = date.today()
    original = today - timedelta(days=2)
    deferred = today + timedelta(days=2)
    bill = Bill(name="Water", amount=45, due_date=original, recurrence="monthly")
    session.add(bill)
    session.commit()
    record_approved_bill_deferral(
        session, bill, original_due_date=original, deferred_due_date=deferred,
        payee_approved=True,
    )
    assert not any(row["bill_id"] == bill.id for row in list_unpaid_past_due(session, today))
    overdue = list_unpaid_past_due(session, deferred + timedelta(days=1))
    assert any(row["bill_id"] == bill.id and row["due_date"] == deferred.isoformat() for row in overdue)


def test_bill_deferral_api_requires_approval_and_rejects_second_change():
    today = date.today()
    original = today + timedelta(days=3)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        bill = Bill(name="Service", amount=30, due_date=original, recurrence="monthly")
        session.add(bill)
        session.commit()
        app.dependency_overrides[get_db] = lambda: session
        try:
            client = TestClient(app)
            path = f"/api/bills/{bill.id}/approved-deferral"
            payload = {
                "original_due_date": original.isoformat(),
                "deferred_due_date": (original + timedelta(days=4)).isoformat(),
                "payee_approved": False,
            }
            assert client.post(path, json=payload).status_code == 400
            payload["payee_approved"] = True
            accepted = client.post(path, json=payload)
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["approved_deferral"]["original_due_date"] == original.isoformat()
            repeat = {**payload, "deferred_due_date": (original + timedelta(days=5)).isoformat()}
            assert client.post(path, json=repeat).status_code == 400
        finally:
            app.dependency_overrides.pop(get_db, None)


def test_cash_profile_rejects_cushion_larger_than_recorded_cash():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        app.dependency_overrides[get_db] = lambda: session
        try:
            client = TestClient(app)
            response = client.post("/api/profile/borrower", json={
                "checking_balance": 100,
                "emergency_fund_balance": 0,
                "target_buffer": 1_000,
                "cash_cushion_balance": 200,
            })
            assert response.status_code == 400
            assert session.exec(select(BorrowerProfile)).first() is None
            accepted = client.post("/api/profile/borrower", json={
                "checking_balance": 100,
                "emergency_fund_balance": 0,
                "target_buffer": 1_000,
                "cash_cushion_balance": 50,
            })
            assert accepted.status_code == 200
            assert accepted.json()["cash_cushion_balance"] == 50
        finally:
            app.dependency_overrides.pop(get_db, None)
