"""Owner-reported zero spending and overdraft regressions."""

from datetime import date, timedelta

import pytest

from app.config import settings
from app.core.cash_rescue import build_cash_rescue_plan
from app.core.cashflow_engine import _resolve_checking_balance
from app.core.entry_validation import validate_cash_balance
from app.core.liquidity_planner import _apply_mortgage_grace_when_needed, _CashEvent, build_liquidity_plan
from app.core.money_state import build_money_state
from app.core.v2_today import _safety_status
from app.db.models import Bill, BorrowerProfile, BudgetCategory, LiquidAccount, MortgageAccount


@pytest.mark.parametrize("balance", [0, -125.75])
def test_recorded_nonpositive_balance_is_never_replaced(session, monkeypatch, balance):
    monkeypatch.setattr(settings, "starting_cash", 5000)
    session.add(BorrowerProfile(checking_balance=balance))
    session.commit()
    assert _resolve_checking_balance(session, None) == balance


def test_negative_account_without_profile_is_preserved(session, monkeypatch):
    monkeypatch.setattr(settings, "starting_cash", 5000)
    session.add(LiquidAccount(account_name="Checking", account_type="checking", balance=-50))
    session.commit()
    assert _resolve_checking_balance(session, None) == -50


@pytest.mark.parametrize("balance", [0, -10])
def test_no_cash_cannot_have_safe_status(balance):
    assert _safety_status({"risk_level": "safe", "checking_balance": balance}) == "at_risk"


@pytest.mark.parametrize("coverage,free,expected", [
    ("covered", 0, "tight"),
    ("covered", 50, "safe"),
    ("unknown", 50, "tight"),
    ("shortfall", 50, "at_risk"),
])
def test_status_accounts_for_actual_spending_headroom(coverage, free, expected):
    assert _safety_status({
        "risk_level": "safe", "checking_balance": 1000,
        "money_state": {"today_breakdown": {
            "coverage_status": coverage, "total_flexible_30_days": free,
            "dated_lowest_after_normal_spending": 100,
        }},
    }) == expected


def test_living_cost_shortfall_cannot_have_safe_status():
    assert _safety_status({
        "risk_level": "safe", "checking_balance": 1000,
        "money_state": {"today_breakdown": {
            "coverage_status": "covered", "total_flexible_30_days": 0,
            "dated_lowest_after_normal_spending": -25,
        }},
    }) == "at_risk"


@pytest.mark.parametrize("cash,low,expected", [(800, 101.25, 1.25), (1000, 600, 500), (80, 600, 0), (500, -25, 0)])
def test_free_to_use_is_entire_dated_headroom_after_one_100_cushion(session, cash, low, expected):
    today = date.today()
    session.add(BorrowerProfile(checking_balance=cash, target_buffer=100, cash_cushion_balance=42.75))
    session.add(BudgetCategory(name="Essentials", monthly_limit=300))
    session.commit()
    state = build_money_state(
        session, as_of=today, starting_cash=cash, buffer_target=42.75,
        horizons={30: {"flexible_today": 1000, "lowest_balance": low + 10}},
        daily_series=[{"date": today.isoformat(), "balance": low + 10}],
        planned_variable_spending=10,
        planned_spending_schedule=[{"date": today.isoformat(), "amount": 10}],
    )
    assert state["total_flexible_30_days"] == expected
    assert state["discretionary_spending_30_days"] == expected
    assert state["working_safety_holdback"] == 100
    assert state["cash_cushion_refill"] == 0
    if expected > 0:
        assert round(min(cash, low) - expected, 2) == 100
        assert round(min(cash, low) - expected - .01, 2) < 100


def test_existing_overdraft_has_gap_even_if_next_income_recovers_it():
    today = date.today()
    rescue = build_cash_rescue_plan(
        cash=-50, events=[{"date": (today + timedelta(days=1)).isoformat(),
                          "kind": "income", "amount": 1000, "name": "Pay", "category": "income"}],
        as_of=today, normal_spending_reserve=0, normal_spending_entered=True,
        cushion_target=100,
    )
    assert rescue["first_gap"]["date"] == today.isoformat()
    assert rescue["first_gap"]["amount"] == 50


def test_overdraft_can_be_entered_without_allowing_negative_reserves():
    assert validate_cash_balance({"checking_balance": -125.75}).ok
    assert not validate_cash_balance({"checking_balance": float("nan")}).ok
    assert not validate_cash_balance({"checking_balance": 0, "target_buffer": -100}).ok


def test_bill_in_month_five_restricts_free_to_use_now(session):
    today = date.today()
    session.add(BorrowerProfile(checking_balance=3000, target_buffer=100))
    session.add(BudgetCategory(name="Daily essentials", usual_amount=1, monthly_limit=31, period="day"))
    session.add(Bill(name="Annual obligation", amount=2500, due_date=today + timedelta(days=130), recurrence="once"))
    session.commit()
    state = build_liquidity_plan(session)["money_state"]
    plan = state["payment_plan"]
    assert state["planning_horizon_days"] >= 181
    assert plan["through"] == state["planning_through"]
    assert plan["lowest_checking"] == state["dated_lowest_after_normal_spending"]
    assert state["free_to_use_now"] == round(plan["lowest_checking"] - 100, 2)
    assert state["free_to_use_now"] < 400


@pytest.mark.parametrize("cash,income_day,use_grace", [
    (300, 10, True), (1000, 10, False), (300, 16, False), (300, 15, True),
])
def test_mortgage_grace_only_when_needed_and_within_recorded_deadline(session, cash, income_day, use_grace):
    today = date(2026, 11, 1)
    due = today.replace(day=5)
    late = today.replace(day=15)
    mortgage = MortgageAccount(lender="Example", original_balance=10000, current_balance=9000,
                               interest_rate=5, monthly_payment=500, due_by_date=due, late_by_date=late)
    session.add(mortgage)
    session.commit()
    payment = _CashEvent(due, "obligation", 500, "Mortgage", "mortgage", mortgage.id, due)
    events = [payment, _CashEvent(today.replace(day=income_day), "income", 1000, "Pay", "income")]
    _apply_mortgage_grace_when_needed(session, events, [], cash, today)
    assert payment.grace_used is use_grace
    assert payment.due_date == due
    assert payment.when == (today.replace(day=income_day) if use_grace else due)
    assert session.get(MortgageAccount, mortgage.id).due_by_date == due


def test_grace_is_not_used_just_to_increase_discretionary_money(session):
    today = date(2026, 11, 1)
    due = today.replace(day=5)
    mortgage = MortgageAccount(lender="Example", original_balance=10000, current_balance=9000,
                               interest_rate=5, monthly_payment=500, due_by_date=due,
                               late_by_date=today.replace(day=15))
    session.add(mortgage)
    session.commit()
    payment = _CashEvent(due, "obligation", 500, "Mortgage", "mortgage", mortgage.id, due)
    events = [payment, _CashEvent(today.replace(day=10), "income", 1000, "Pay", "income")]
    _apply_mortgage_grace_when_needed(session, events, [], 550, today)
    assert not payment.grace_used
    assert payment.when == due
