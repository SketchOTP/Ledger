"""Directive 001 acceptance coverage for the V2 rolling capital contract."""

from datetime import date, timedelta

from app.core.financial_snapshot import build_financial_snapshot
from app.core.goal_planner import build_goal_plan, can_contribute_to_goals
from app.core.liquidity_planner import build_liquidity_plan
from app.core.stress_forecast import build_stress_forecast
from app.db.models import Bill, BorrowerProfile, IncomeSource, SavingsGoal, Transaction


def test_literal_dates_cross_year_without_budget_month_reassignment(session):
    as_of = date(2026, 11, 20)
    session.add(
        IncomeSource(
            source_name="Biweekly",
            amount=2000,
            pay_frequency="biweekly",
            next_pay_date=date(2026, 11, 30),
        )
    )
    session.add(
        Bill(
            name="December rent",
            amount=1200,
            due_date=date(2026, 12, 2),
            recurrence="monthly",
            required=True,
        )
    )
    session.commit()

    plan = build_liquidity_plan(session, starting_cash=3000, as_of=as_of)
    dates = [(row["name"], row["date"]) for row in plan["canonical_timeline"]]
    assert ("Biweekly", "2026-11-30") in dates
    assert ("December rent", "2026-12-02") in dates
    assert any(row[1].startswith("2027-01") for row in dates)


def test_longer_horizon_constrains_capital_that_looks_available_at_30_days(session):
    today = date.today()
    session.add(BorrowerProfile(target_buffer=100))
    session.add(
        IncomeSource(
            source_name="Pay",
            amount=3000,
            pay_frequency="biweekly",
            next_pay_date=today + timedelta(days=45),
        )
    )
    session.add(
        Bill(
            name="Known repair",
            amount=5000,
            due_date=today + timedelta(days=50),
            recurrence="once",
            required=True,
        )
    )
    session.commit()

    plan = build_liquidity_plan(session, starting_cash=3000)
    state = plan["money_state"]
    assert plan["horizons"][30]["flexible_today"] > plan["horizons"][90]["flexible_today"]
    assert state["constraining_horizon"] in {"60", "90", "180", "6mo"}
    assert state["deployable_surplus"] <= plan["horizons"][90]["flexible_today"]


def test_uncertainty_reserve_uses_conservative_fallback_then_grows_with_variance(session):
    today = date.today()
    session.add(BorrowerProfile(target_buffer=100))
    session.commit()
    baseline = build_liquidity_plan(session, starting_cash=8000)["money_state"]
    assert baseline["uncertainty_confidence"] == "low"
    for offset, amount in enumerate((20, 25, 20, 500, 30, 600, 25, 700)):
        session.add(
            Transaction(
                amount=amount,
                category="ordinary",
                transaction_date=today - timedelta(days=offset * 7 + 1),
            )
        )
    session.commit()
    volatile = build_liquidity_plan(session, starting_cash=8000)["money_state"]
    assert volatile["uncertainty_reserve"] > baseline["uncertainty_reserve"]
    assert volatile["uncertainty_confidence"] in {"medium", "high"}
    assert any(
        item["source"] == "recent_spending_volatility"
        for item in volatile["provenance"]["uncertainty_reserve"]
    )


def test_tight_household_cannot_create_deployable_capital(session):
    today = date.today()
    session.add(BorrowerProfile(target_buffer=250))
    session.add(
        Bill(name="Rent", amount=1000, due_date=today + timedelta(days=2), required=True)
    )
    session.commit()
    state = build_liquidity_plan(session, starting_cash=900)["money_state"]
    assert state["deployable_surplus"] == 0
    assert state["experimental_capital"] == 0


def test_snapshot_stress_and_goals_consume_the_same_v2_contract(session):
    today = date.today()
    session.add(
        BorrowerProfile(
            checking_balance=15000, target_buffer=500, risk_tolerance="moderate"
        )
    )
    session.add(
        IncomeSource(
            source_name="Pay",
            amount=3000,
            pay_frequency="biweekly",
            next_pay_date=today + timedelta(days=7),
        )
    )
    session.add(
        Bill(name="Rent", amount=1000, due_date=today + timedelta(days=3), required=True)
    )
    session.add(SavingsGoal(name="Future", target_amount=5000, enabled=True))
    session.commit()

    snapshot = build_financial_snapshot(session)
    state = snapshot.money_state
    assert state == snapshot.cashflow["money_state"]
    assert state["experimental_capital"] <= state["deployable_surplus"]
    forecast = build_stress_forecast(snapshot, horizon_days=90)
    assert len(forecast["timeline"]) == 90
    assert can_contribute_to_goals(snapshot.cashflow) == (state["deployable_surplus"] > 0)
    plan = build_goal_plan(session, snapshot.cashflow)
    assert plan["capital_contract"] == "v2_deployable_surplus"
    assert plan["available_surplus"] == state["deployable_surplus"]
