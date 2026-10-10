"""Coverage for the bounded long-range cash-flow outlook options."""

from datetime import date, timedelta

from app.core.cashflow_plan import CASHFLOW_PLAN_HORIZONS, build_cashflow_plan


def test_long_horizons_use_the_same_literal_date_projection(session):
    plan = build_cashflow_plan(session, horizon_days=3652)

    assert plan["horizon_days"] == 3652
    assert len(plan["daily_series"]) == 3653
    assert plan["daily_series"][-1]["date"] == (date.today() + timedelta(days=3652)).isoformat()
    assert {row["day"] for row in plan["milestones"]} == set(CASHFLOW_PLAN_HORIZONS)


def test_supported_horizon_constants_are_bounded_and_ordered():
    assert CASHFLOW_PLAN_HORIZONS == (30, 60, 90, 180, 182, 365, 913, 1826, 3652)
