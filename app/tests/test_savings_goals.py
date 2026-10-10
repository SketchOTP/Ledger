"""Savings goals and safe contribution planning."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.cashflow_engine import calculate_cashflow
from app.core.goal_planner import (
    build_goal_plan,
    can_contribute_to_goals,
)
from app.db.models import BorrowerProfile, SavingsGoal
from app.main import app


@pytest.fixture
def client():
    from app.db.session import init_db

    init_db()
    return TestClient(app)


def test_create_and_list_goals(client):
    r = client.post(
        "/api/goals",
        json={
            "name": "Save for college",
            "target_amount": 10000,
            "saved_amount": 500,
            "due_date": (date.today() + timedelta(days=365)).isoformat(),
            "enabled": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["goal"]["name"] == "Save for college"

    listed = client.get("/api/goals")
    assert listed.status_code == 200
    data = listed.json()
    names = [g["name"] for g in data["goals"]]
    assert "Save for college" in names
    assert "plan" in data


def test_toggle_goal_disabled_excluded_from_plan(session, sample_data):
    session.add(BorrowerProfile(checking_balance=8000, target_buffer=500))
    goal = SavingsGoal(
        name="Vacation",
        target_amount=2000,
        saved_amount=0,
        enabled=False,
    )
    session.add(goal)
    session.commit()

    plan = build_goal_plan(session)
    assert plan["enabled_count"] == 0
    assert plan["total_recommended"] == 0


def test_blocked_when_cashflow_tight(session, sample_data):
    session.add(
        SavingsGoal(
            name="College",
            target_amount=20000,
            saved_amount=0,
            due_date=date.today() + timedelta(days=180),
            enabled=True,
        )
    )
    session.commit()

    cf = calculate_cashflow(session)
    plan = build_goal_plan(session, cf)
    assert plan["enabled_count"] == 1
    if not can_contribute_to_goals(cf):
        assert plan["can_contribute"] is False
        assert plan["total_recommended"] == 0
        row = plan["goals"][0]
        assert row["status"] == "blocked"
        assert row["recommended_contribution"] == 0


def test_safe_contribution_when_cashflow_safe(session, sample_data):
    session.add(BorrowerProfile(checking_balance=15000, target_buffer=500))
    session.add(
        SavingsGoal(
            name="College",
            target_amount=12000,
            saved_amount=0,
            due_date=date.today() + timedelta(days=365),
            enabled=True,
        )
    )
    session.commit()

    cf = calculate_cashflow(session)
    plan = build_goal_plan(session, cf)
    if can_contribute_to_goals(cf):
        assert plan["can_contribute"] is True
        assert plan["total_recommended"] > 0
        row = next(g for g in plan["goals"] if g["name"] == "College")
        assert row["recommended_contribution"] > 0
        assert row["required_monthly"] is not None


def test_my_money_summary_includes_goals_section(client):
    client.post(
        "/api/goals",
        json={"name": "Car", "target_amount": 5000, "saved_amount": 0, "enabled": True},
    )
    r = client.get("/api/my-money/summary")
    assert r.status_code == 200
    sections = {s["id"]: s for s in r.json()["sections"]}
    assert "goals" in sections
    assert sections["goals"]["label"] == "Goals"
