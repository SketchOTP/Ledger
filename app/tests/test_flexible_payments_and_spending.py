"""Owner-entered daily/weekly spending and finite dated bill plans."""

import calendar
import json
from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_db
from app.core.bill_payment import bill_payment_summary, mark_bill_paid, sum_bills_due_in_period_unpaid
from app.core.bill_schedule import expand_bill_occurrences
from app.core.budget_engine import budget_for_month, planned_spending_daily_events, planned_spending_in_window
from app.core.entry_validation import validate_bill
from app.core.misc_schedule import sum_misc_due_in_period
from app.core.payment_restore import restore_marked_payment
from app.db.models import Bill, BudgetCategory, MiscOccurrence, MiscSpending
from app.db.session import _sqlite_migrate_flexible_payments
from app.main import app


def _dated_plan(today: date) -> list[dict[str, object]]:
    return [
        {"due_date": (today + timedelta(days=2)).isoformat(), "amount": 20},
        {"due_date": (today + timedelta(days=17)).isoformat(), "amount": 25},
        {"due_date": (today + timedelta(days=36)).isoformat(), "amount": 30},
    ]


def test_three_payment_plan_uses_exact_dates_and_amounts(session):
    today = date.today()
    payments = _dated_plan(today)
    bill = Bill(
        name="Three-part purchase", amount=20,
        due_date=date.fromisoformat(payments[0]["due_date"]),
        recurrence="scheduled", schedule_json=json.dumps(payments),
    )
    session.add(bill)
    session.commit()

    assert expand_bill_occurrences(bill, today, today + timedelta(days=90)) == [
        (date.fromisoformat(row["due_date"]), row["amount"]) for row in payments
    ]
    assert sum_bills_due_in_period_unpaid(session, today, today + timedelta(days=90)) == 75
    assert sum_bills_due_in_period_unpaid(session, today, today + timedelta(days=30)) == 45

    _, next_due = mark_bill_paid(session, bill, date.fromisoformat(payments[0]["due_date"]))
    assert next_due == date.fromisoformat(payments[1]["due_date"])
    assert bill_payment_summary(session, bill)["amount"] == 25
    assert sum_bills_due_in_period_unpaid(session, today, today + timedelta(days=90)) == 55
    assert len(bill_payment_summary(session, bill)["payments"]) == 3

    restored = restore_marked_payment(session, {
        "bill_id": bill.id, "due_date": date.fromisoformat(payments[0]["due_date"]),
    })
    assert restored["ok"] is True
    assert bill.due_date == date.fromisoformat(payments[0]["due_date"])
    assert bill.amount == 20
    assert sum_bills_due_in_period_unpaid(session, today, today + timedelta(days=90)) == 75


def test_payment_plan_validation_rejects_missing_and_duplicate_dates():
    today = date.today().isoformat()
    data = {"name": "Plan", "amount": 10, "recurrence": "scheduled", "payments": []}
    assert not validate_bill(data).ok
    data["payments"] = [{"due_date": today, "amount": 10}, {"due_date": today, "amount": 20}]
    assert not validate_bill(data).ok


def test_plan_api_rejects_rewriting_paid_installment():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        app.dependency_overrides[get_db] = lambda: session
        client = TestClient(app)
        today = date.today()
        payments = _dated_plan(today)
        payload = {
            "name": "Plan", "amount": 20, "recurrence": "scheduled", "repeats": False,
            "payments": payments,
        }
        try:
            created = client.post("/api/bills", json=payload)
            assert created.status_code == 200, created.text
            row = created.json()
            assert len(row["payments"]) == 3
            assert row["due_date"] == payments[0]["due_date"]
            paid = client.post(f"/api/bills/{row['id']}/mark-paid/quick")
            assert paid.status_code == 200, paid.text
            outside = client.post(f"/api/bills/{row['id']}/mark-paid", json={
                "due_date": (today + timedelta(days=7)).isoformat(),
            })
            assert outside.status_code == 400
            changed = {**payload, "payments": [{**payments[0], "amount": 99}, *payments[1:]]}
            assert client.put(f"/api/bills/{row['id']}", json=changed).status_code == 400
            assert client.put(f"/api/bills/{row['id']}", json=payload).status_code == 200
        finally:
            app.dependency_overrides.pop(get_db, None)


def test_daily_weekly_monthly_spending_uses_calendar_days(session):
    days = calendar.monthrange(2026, 9)[1]
    daily = BudgetCategory(name="Meals", monthly_limit=304.38, usual_amount=10, period="day")
    weekly = BudgetCategory(name="Fun", monthly_limit=43.48, usual_amount=70, period="week")
    monthly = BudgetCategory(name="Other", monthly_limit=50, usual_amount=50, period="month")
    assert budget_for_month(daily, 2026, 9) == 10 * days
    assert budget_for_month(daily, 2026, 10) == 310
    assert budget_for_month(weekly, 2026, 9) == 300
    assert budget_for_month(weekly, 2026, 10) == 310
    assert budget_for_month(monthly, 2026, 9) == 50
    session.add(daily)
    session.commit()
    rows = planned_spending_daily_events(session, date(2026, 9, 1), date(2026, 9, 3))
    assert [row["amount"] for row in rows] == [10, 10, 10]
    assert planned_spending_in_window(session, date(2026, 9, 1), date(2026, 9, 3)) == 30


def test_legacy_monthly_plan_survives_idempotent_migration():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE budget_categories (id TEXT PRIMARY KEY, name TEXT, monthly_limit REAL)"))
        conn.execute(text("CREATE TABLE bills (id TEXT PRIMARY KEY, name TEXT, amount REAL)"))
        conn.execute(text("CREATE TABLE misc_spending (id TEXT PRIMARY KEY, name TEXT, amount REAL)"))
        conn.execute(text("INSERT INTO budget_categories VALUES ('old', 'Groceries', 120)"))
    _sqlite_migrate_flexible_payments(engine)
    _sqlite_migrate_flexible_payments(engine)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT monthly_limit, usual_amount, period FROM budget_categories")).one()
        assert tuple(row) == (120, None, "month")
        assert "schedule_json" in {
            col[1] for col in conn.execute(text("PRAGMA table_info(bills)"))
        }
        assert "active" in {
            col[1] for col in conn.execute(text("PRAGMA table_info(misc_spending)"))
        }


def test_move_misc_to_bill_archives_without_double_counting_or_erasing_history():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        today = date.today()
        item = MiscSpending(
            name="Three-part purchase", category="other", amount=20,
            due_date=today + timedelta(days=2), recurrence="monthly",
        )
        session.add(item)
        session.commit()
        session.add(MiscOccurrence(
            misc_spending_id=item.id, due_date=today - timedelta(days=12),
            amount=20, paid=True, paid_date=today - timedelta(days=12),
        ))
        session.commit()
        app.dependency_overrides[get_db] = lambda: session
        client = TestClient(app)
        payments = _dated_plan(today)
        try:
            moved = client.post(f"/api/misc/{item.id}/move-to-bills", json={
                "name": "Three-part purchase", "amount": 20,
                "recurrence": "scheduled", "payments": payments,
            })
            assert moved.status_code == 200, moved.text
            assert session.get(MiscSpending, item.id).active is False
            assert session.exec(select(MiscOccurrence).where(MiscOccurrence.misc_spending_id == item.id)).first()
            assert client.get("/api/misc").json()["items"] == []
            assert sum_misc_due_in_period(session, today, today + timedelta(days=60)) == 0
            assert sum_bills_due_in_period_unpaid(session, today, today + timedelta(days=60)) == 75
            assert client.post(f"/api/misc/{item.id}/move-to-bills", json={
                "name": "Duplicate", "amount": 20, "recurrence": "once",
                "due_date": (today + timedelta(days=1)).isoformat(),
            }).status_code == 404
        finally:
            app.dependency_overrides.pop(get_db, None)


def test_budget_api_preserves_owner_period_and_legacy_monthly_payload():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        app.dependency_overrides[get_db] = lambda: session
        client = TestClient(app)
        try:
            row = client.post("/api/spending/budget", json={
                "name": "Eating out", "usual_amount": 12, "period": "day",
            })
            assert row.status_code == 200, row.text
            assert row.json()["usual_amount"] == 12
            assert row.json()["period"] == "day"
            edited = client.patch(f"/api/spending/budget/{row.json()['id']}", json={
                "name": "Eating out", "usual_amount": 55, "period": "week",
            })
            assert edited.status_code == 200, edited.text
            assert edited.json()["period"] == "week"
            legacy = client.post("/api/spending/budget", json={"name": "Groceries", "monthly_limit": 100})
            assert legacy.status_code == 200, legacy.text
            assert legacy.json()["period"] == "month"
        finally:
            app.dependency_overrides.pop(get_db, None)
