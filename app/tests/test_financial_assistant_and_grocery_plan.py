"""Weekly groceries and ephemeral Codex advice share dated financial truth."""

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.ai.providers.cli import CodexCLIProvider
from app.api.deps import get_db
from app.core.budget_engine import planned_spending_daily_events, planned_spending_in_window
from app.core.liquidity_planner import build_liquidity_plan
from app.core.user_ai_settings import _from_dict, load_user_ai_settings, save_user_ai_settings
from app.db.models import Bill, BorrowerProfile, BudgetCategory, IncomeSource
from app.main import app


def test_weekly_groceries_have_one_dated_trip_per_week_and_no_double_count(session):
    start = date(2026, 9, 24)  # Thursday; Saturday is the editable default.
    session.add(BudgetCategory(name="Groceries", monthly_limit=435, usual_amount=100,
                               period="week", planned_weekday=5))
    session.add(BudgetCategory(name="Everyday", monthly_limit=750, usual_amount=25,
                               period="day"))
    session.add(BorrowerProfile(checking_balance=2000, target_buffer=0))
    session.add(Bill(name="Rent", amount=500, due_date=start + timedelta(days=10)))
    session.add(IncomeSource(source_name="Pay", amount=1000, pay_frequency="biweekly",
                             next_pay_date=start + timedelta(days=7)))
    session.commit()

    end = start + timedelta(days=30)
    events = planned_spending_daily_events(session, start, end)
    trips = [row for row in events if row.get("spending_kind") == "groceries"]
    assert [row["date"] for row in trips] == [
        "2026-09-26", "2026-10-03", "2026-10-10", "2026-10-17", "2026-10-24",
    ]
    assert all(row["amount"] == 100 for row in trips)
    assert sum(row["amount"] for row in events) == planned_spending_in_window(session, start, end)

    state = build_liquidity_plan(session, as_of=start)["money_state"]
    plan = state["payment_plan_30_days"]
    planned = [row for row in plan["rows"] if row.get("spending_kind") == "groceries"]
    assert len(planned) == 5
    assert state["expected_variable_reserve"] == sum(row["amount"] for row in events)
    assert plan["projected_ending_checking"] == round(
        plan["opening_checking"] + sum(
            row["amount"] if row["kind"] == "income" else -row["amount"]
            for row in plan["rows"]
        ), 2,
    )
    assert all(row["category"] == "living" for row in planned)


def test_changing_grocery_day_changes_dated_cash_pressure(session):
    start = date(2026, 9, 24)
    row = BudgetCategory(name="Groceries", monthly_limit=435, usual_amount=100,
                         period="week", planned_weekday=4)
    session.add(row)
    session.commit()
    friday = planned_spending_daily_events(session, start, start + timedelta(days=7))
    assert [r["date"] for r in friday if r.get("spending_kind") == "groceries"] == ["2026-09-25"]
    row.planned_weekday = 5
    session.add(row)
    session.commit()
    saturday = planned_spending_daily_events(session, start, start + timedelta(days=7))
    assert [r["date"] for r in saturday if r.get("spending_kind") == "groceries"] == ["2026-09-26"]


def test_financial_assistant_uses_snapshot_without_storing_chat(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    seen = {}
    monkeypatch.setattr(CodexCLIProvider, "health_check", lambda self: {"ok": True})

    def answer(_self, prompt):
        seen["prompt"] = prompt
        return "Your example bill is due in three days."

    monkeypatch.setattr(CodexCLIProvider, "generate_reply", answer)
    with Session(engine) as session:
        session.add(BorrowerProfile(checking_balance=900, target_buffer=100))
        session.add(Bill(name="Example bill", amount=125, due_date=date.today() + timedelta(days=3)))
        session.commit()
        app.dependency_overrides[get_db] = lambda: session
        try:
            client = TestClient(app)
            response = client.post("/api/ai/financial-assistant", json={
                "question": "When is my example bill due?", "history": [],
            })
            assert response.status_code == 200, response.text
            assert response.json()["saved"] is False
            assert "Example bill" in seen["prompt"]
            assert "125" in seen["prompt"]
            assert "Do not make changes" in seen["prompt"]
            assert "only its remaining amount" in seen["prompt"]
            assert "at least six calendar months" in seen["prompt"]
            assert session.exec(select(Bill)).one().amount == 125
        finally:
            app.dependency_overrides.clear()


def test_codex_preferences_survive_reloading_settings(tmp_path, monkeypatch):
    from app.core import user_ai_settings

    path = tmp_path / "ai_settings.json"
    monkeypatch.setattr(user_ai_settings, "_settings_path", lambda _user_id: path)
    save_user_ai_settings("example", {
        "ai_provider": "future_cli_codex", "ai_model": "gpt-6-sol",
        "ai_reasoning_effort": "medium", "assistant_model": "gpt-6-luna",
        "assistant_reasoning_effort": "high",
    })
    settings = _from_dict(load_user_ai_settings("example"))
    assert settings.ai_model == "gpt-6-sol"
    assert settings.ai_reasoning_effort == "medium"
    assert settings.assistant_model == "gpt-6-luna"
    assert settings.assistant_reasoning_effort == "high"
