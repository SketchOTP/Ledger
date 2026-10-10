"""Due Soon spending and checking reconciliation use one truthful cash timeline."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_db
from app.core.budget_engine import planned_spending_daily_events, spending_attention
from app.core.liquidity_planner import build_liquidity_plan
from app.core.v2_today import build_today
from app.db.models import (
    Bill,
    BorrowerProfile,
    BudgetCategory,
    CashCushionContribution,
    IncomeSource,
    Transaction,
)
from app.db.session import _sqlite_migrate_checking_confirmation, _sqlite_migrate_recorded_spending
from app.main import app


def test_daily_and_grocery_purchases_stay_reserved_until_checking_reflects_them(session):
    today = date.today()
    previous_saturday = today - timedelta(days=((today.weekday() - 5) % 7) or 7)
    groceries = BudgetCategory(name="Groceries", monthly_limit=435, usual_amount=100,
                               period="week", planned_weekday=5)
    daily = BudgetCategory(name="Everyday", monthly_limit=750, usual_amount=25, period="day")
    session.add(groceries)
    session.add(daily)
    session.commit()
    session.add(Transaction(amount=80, category="Groceries", transaction_date=previous_saturday,
                            planned_budget_category_id=groceries.id,
                            planned_spending_date=previous_saturday))
    session.add(Transaction(amount=10, category="Everyday", transaction_date=today,
                            planned_budget_category_id=daily.id, planned_spending_date=today))
    session.commit()
    rows = planned_spending_daily_events(session, today, today + timedelta(days=7))
    assert next(row for row in rows if row["spending_kind"] == "recorded_pending")["amount"] == 80
    assert next(row for row in rows if row["spending_kind"] == "daily")["amount"] == 25
    assert spending_attention(session, today)["pending_checking_count"] == 2

    for transaction in session.exec(select(Transaction)).all():
        transaction.reflected_in_checking = True
        session.add(transaction)
    session.commit()
    rows = planned_spending_daily_events(session, today, today + timedelta(days=7))
    assert not any(row["spending_kind"] == "recorded_pending" for row in rows)
    assert next(row for row in rows if row["spending_kind"] == "daily")["amount"] == 15
    assert spending_attention(session, today)["pending_checking_count"] == 0


def test_current_grocery_trip_replaces_estimate_and_then_leaves_forecast(session):
    saturday = date(2026, 9, 26)
    groceries = BudgetCategory(name="Groceries", monthly_limit=435, usual_amount=100,
                               period="week", planned_weekday=5)
    session.add(groceries)
    session.commit()
    session.add(Transaction(amount=80, category="Groceries", transaction_date=saturday,
                            planned_budget_category_id=groceries.id,
                            planned_spending_date=saturday))
    session.commit()
    rows = planned_spending_daily_events(session, saturday, saturday)
    assert [row["amount"] for row in rows if row["spending_kind"] == "groceries"] == [80]
    transaction = session.exec(select(Transaction)).one()
    transaction.reflected_in_checking = True
    session.add(transaction)
    session.commit()
    rows = planned_spending_daily_events(session, saturday, saturday)
    assert not any(row["spending_kind"] == "groceries" for row in rows)


def test_record_spent_does_not_change_checking_and_reconciles_only_on_confirmation():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        profile = BorrowerProfile(checking_balance=1000, target_buffer=0, cash_cushion_balance=0)
        groceries = BudgetCategory(name="Groceries", monthly_limit=435, usual_amount=100,
                                   period="week", planned_weekday=5)
        session.add(profile)
        session.add(groceries)
        session.commit()
        previous_saturday = date.today() - timedelta(days=(date.today().weekday() - 5) % 7)
        app.dependency_overrides[get_db] = lambda: session
        try:
            client = TestClient(app)
            payload = {"budget_category_id": groceries.id,
                       "planned_date": previous_saturday.isoformat(),
                       "transaction_date": date.today().isoformat(), "amount": 80}
            recorded = client.post("/api/spending/record-planned", json=payload)
            assert recorded.status_code == 200, recorded.text
            assert session.exec(select(BorrowerProfile)).first().checking_balance == 1000
            assert client.post("/api/spending/record-planned", json=payload).status_code == 409
            assert client.get("/api/v2/today").json()["spending_attention"]["pending_checking_count"] == 1
            update = client.post("/api/profile/borrower", json={
                "checking_balance": 1000, "checking_balance_confirmed": True,
                "includes_recorded_spending": False,
            })
            assert update.status_code == 200, update.text
            pending = client.get("/api/v2/today").json()
            assert pending["spending_attention"]["pending_checking_count"] == 1
            assert sum(row.get("pending_checking_amount", 0)
                       for row in pending["financial_state"]["payment_plan"]["rows"]) == 80
            update = client.post("/api/profile/borrower", json={"checking_balance": 920})
            assert update.status_code == 200, update.text
            assert session.exec(select(Transaction)).one().reflected_in_checking is False
            update = client.post("/api/profile/borrower", json={
                "checking_balance": 920, "includes_recorded_spending": True,
            })
            assert update.status_code == 200, update.text
            assert session.exec(select(Transaction)).one().reflected_in_checking is True
            assert client.get("/api/v2/today").json()["spending_attention"]["pending_checking_count"] == 0
        finally:
            app.dependency_overrides.clear()


@pytest.mark.parametrize("days_after_purchase", [0, 1])
def test_grocery_plan_identifies_pending_cash_and_deducts_purchase_exactly_once(session, days_after_purchase):
    saturday = date(2026, 10, 10)
    as_of = saturday + timedelta(days=days_after_purchase)
    profile = BorrowerProfile(checking_balance=1000, target_buffer=0, cash_cushion_balance=0)
    groceries = BudgetCategory(name="Groceries", monthly_limit=435, usual_amount=100,
                               period="week", planned_weekday=5)
    session.add(profile)
    session.add(groceries)
    session.commit()
    purchase = Transaction(amount=80, category="Groceries", transaction_date=saturday,
                           planned_budget_category_id=groceries.id, planned_spending_date=saturday)
    session.add(purchase)
    session.commit()
    before = build_liquidity_plan(session, as_of=as_of)["money_state"]["payment_plan"]
    pending = [row for row in before["rows"] if row.get("awaiting_checking_update")]
    assert len(pending) == 1
    assert pending[0]["amount"] == pending[0]["pending_checking_amount"] == 80
    assert pending[0]["spending_kind"] == ("groceries" if days_after_purchase == 0 else "recorded_pending")
    if days_after_purchase:
        assert pending[0]["pending_grocery_amount"] == 80
    assert next(row for row in spending_attention(session, as_of)["grocery_trips"]
                if row["planned_date"] == saturday.isoformat())["awaiting_checking_update"] is True

    purchase.reflected_in_checking = True
    profile.checking_balance = 920
    session.add(purchase)
    session.add(profile)
    session.commit()
    after = build_liquidity_plan(session, as_of=as_of)["money_state"]["payment_plan"]
    assert not any(row.get("awaiting_checking_update") for row in after["rows"])
    assert after["projected_ending_checking"] == before["projected_ending_checking"]
    assert spending_attention(session, as_of)["pending_checking_count"] == 0
    assert any(row.get("spending_kind") == "groceries" and row["amount"] == 100 for row in after["rows"])


def test_scheduled_activity_requires_new_checking_confirmation(session):
    now = date.today()
    profile = BorrowerProfile(checking_balance=600, checking_confirmed_at=None)
    session.add(profile)
    session.add(Bill(name="Water", amount=50, due_date=now, recurrence="monthly"))
    session.commit()
    from datetime import UTC, datetime

    profile.checking_confirmed_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    session.add(profile)
    session.commit()
    from app.core.payment_archive_log import log_payment_marked

    log_payment_marked(session, action="marked_paid", kind="bill", name="Water",
                       amount=50, scheduled_date=now, marked_date=now)
    assert build_today(session)["checking_activity_pending"] == 1
    profile.checking_confirmed_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=1)
    session.add(profile)
    session.commit()
    assert build_today(session)["checking_activity_pending"] == 0


def test_today_lists_the_full_30_day_bill_and_income_checklist(session):
    today = date.today()
    session.add(BorrowerProfile(checking_balance=5000))
    for index in range(9):
        session.add(Bill(name=f"Bill {index}", amount=10, due_date=today + timedelta(days=index + 1)))
    session.add(IncomeSource(source_name="Paycheck", amount=500,
                             next_pay_date=today + timedelta(days=25)))
    session.commit()
    rows = build_today(session)["immediate_obligations"]
    assert len([row for row in rows if row["name"].startswith("Bill ")]) >= 9
    assert any(row["kind"] == "income" and row["due_date"] == (today + timedelta(days=25)).isoformat()
               for row in rows)


def test_marked_bill_hides_optional_money_until_checking_is_confirmed():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(BorrowerProfile(checking_balance=1000, target_buffer=100,
                                    cash_cushion_balance=100))
        bill = Bill(name="Water", amount=50, due_date=date.today(), recurrence="monthly")
        session.add(bill)
        session.commit()
        app.dependency_overrides[get_db] = lambda: session
        try:
            client = TestClient(app)
            response = client.post("/api/obligations/mark-paid", json={
                "bill_id": bill.id, "due_date": date.today().isoformat(), "amount": 50,
            })
            assert response.status_code == 200, response.text
            assert client.get("/api/v2/today").json()["checking_activity_pending"] == 1
            response = client.post("/api/profile/borrower", json={
                "checking_balance": 950, "checking_balance_confirmed": True,
            })
            assert response.status_code == 200, response.text
            assert client.get("/api/v2/today").json()["checking_activity_pending"] == 0
        finally:
            app.dependency_overrides.clear()


def test_cushion_step_is_once_per_cycle_and_does_not_move_cash():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(BorrowerProfile(checking_balance=5000, target_buffer=1000,
                                    cash_cushion_balance=200))
        session.add(BudgetCategory(name="Groceries", monthly_limit=300))
        session.commit()
        app.dependency_overrides[get_db] = lambda: session
        try:
            client = TestClient(app)
            suggested = client.get("/api/v2/today").json()["cushion_step"]["amount"]
            assert suggested == 0  # The goal is protected once, without an automatic contribution.
            amount = 25  # The owner may still earmark money explicitly inside that reserve.
            first = client.post("/api/profile/cushion-contribution", json={"amount": amount})
            assert first.status_code == 200, first.text
            assert session.exec(select(BorrowerProfile)).one().checking_balance == 5000
            assert session.exec(select(BorrowerProfile)).one().cash_cushion_balance == 200 + amount
            assert session.exec(select(CashCushionContribution)).one().amount == amount
            assert client.post("/api/profile/cushion-contribution", json={"amount": amount}).status_code == 409
            assert client.get("/api/v2/today").json()["cushion_step"]["recorded"] is True
        finally:
            app.dependency_overrides.clear()


def test_existing_sqlite_tables_migrate_without_replaying_old_payment_marks():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE transactions (id TEXT PRIMARY KEY, amount FLOAT)"))
        connection.execute(text("CREATE TABLE borrower_profile (id TEXT PRIMARY KEY, updated_at DATETIME)"))
        connection.execute(text("INSERT INTO borrower_profile VALUES ('owner', '2026-01-01 00:00:00')"))
    _sqlite_migrate_recorded_spending(engine)
    _sqlite_migrate_checking_confirmation(engine)
    with engine.connect() as connection:
        transaction_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(transactions)"))}
        profile_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(borrower_profile)"))}
        assert {"planned_budget_category_id", "planned_spending_date", "reflected_in_checking"} <= transaction_columns
        assert "checking_confirmed_at" in profile_columns
        assert connection.execute(text("SELECT checking_confirmed_at FROM borrower_profile")).scalar_one()
