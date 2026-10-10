"""Dated discretionary planning without front-loading living costs or the cushion goal."""

from datetime import date, timedelta

from sqlmodel import select

from app.core.budget_engine import planned_spending_in_window, remaining_monthly_budget
from app.core.liquidity_planner import build_liquidity_plan
from app.core.required_payment_deferral import record_approved_payment_deferral
from app.db.models import (
    Bill,
    BorrowerProfile,
    BudgetCategory,
    CreditCard,
    Debt,
    IncomeSource,
    MiscSpending,
    MortgageAccount,
)


def test_daily_and_weekly_usual_spending_only_counts_future_days(session):
    session.add(BudgetCategory(name="Food", monthly_limit=750, usual_amount=25, period="day"))
    session.add(BudgetCategory(name="Travel", monthly_limit=300, usual_amount=70, period="week"))
    session.add(BudgetCategory(name="Other", monthly_limit=300, usual_amount=300, period="month"))
    session.commit()

    # Seven days remain in September. The daily and weekly habits are future
    # rates, not a whole month's unspent purchases due before the next payday.
    assert remaining_monthly_budget(session, date(2026, 9, 24)) == 545
    assert planned_spending_in_window(session, date(2026, 9, 24), date(2026, 9, 30)) == 545


def test_payment_plan_reconciles_approved_deferral_living_costs_and_small_refill(session):
    today = date(2026, 9, 24)
    original = date(2026, 10, 5)
    deferred = date(2026, 10, 7)
    mortgage = MortgageAccount(
        lender="Example lender", original_balance=100_000, current_balance=90_000,
        interest_rate=5, monthly_payment=800, due_by_date=original,
    )
    session.add(BorrowerProfile(
        checking_balance=1_600, target_buffer=500, cash_cushion_balance=0,
    ))
    session.add(BudgetCategory(
        name="Everyday", monthly_limit=300, usual_amount=10, period="day",
    ))
    session.add(Bill(name="Required bill", amount=400, due_date=today + timedelta(days=3)))
    session.add(Debt(
        creditor="Loan", balance=1_000, apr=9, minimum_payment=60,
        due_date=today + timedelta(days=16), debt_type="personal_loan",
    ))
    session.add(CreditCard(
        issuer="Bank", card_name="Card", balance=500, credit_limit=2_000,
        apr=20, minimum_payment=40, due_date=today + timedelta(days=18),
    ))
    session.add(MiscSpending(
        name="Other scheduled payment", amount=25,
        due_date=today + timedelta(days=5), recurrence="once",
    ))
    session.add(IncomeSource(
        source_name="Paycheck", amount=1_000, pay_frequency="biweekly",
        next_pay_date=deferred,
    ))
    session.add(mortgage)
    session.commit()
    record_approved_payment_deferral(
        session, "mortgage", mortgage.id, original_due_date=original,
        deferred_due_date=deferred, payee_approved=True, as_of=today,
    )

    state = build_liquidity_plan(session, as_of=today)["money_state"]
    plan = state["payment_plan"]
    mortgage_rows = [row for row in plan["rows"] if row["category"] == "mortgage"]
    assert mortgage_rows[0]["date"] == deferred.isoformat()
    assert mortgage_rows[0]["original_due_date"] == original.isoformat()
    assert mortgage_rows[0]["deferred_once"] is True
    assert not any(row["date"] == original.isoformat() for row in mortgage_rows)
    same_day = [row for row in plan["rows"] if row["date"] == deferred.isoformat()]
    assert same_day[0]["kind"] == "income"
    assert same_day[1]["category"] == "living"
    assert same_day[0]["projected_balance_after"] >= mortgage_rows[0]["amount"]

    signed_total = sum(
        row["amount"] if row["kind"] == "income" else -row["amount"]
        for row in plan["rows"]
    )
    assert round(plan["opening_checking"] + signed_total, 2) == plan["projected_ending_checking"]
    assert plan["lowest_checking"] == state["dated_lowest_after_normal_spending"]
    assert state["cash_cushion_current"] == 0
    assert state["cash_cushion_goal"] == 500
    assert state["cash_cushion_refill"] == 0  # Full goal is held once, not charged again.
    assert 0 < state["discretionary_spending_30_days"]
    assert state["flexible_safety_remaining"] == 0
    assert state["discretionary_spending_30_days"] >= state["deployable_surplus"]
    assert state["total_flexible_30_days"] == round(
        state["discretionary_spending_30_days"] + state["flexible_safety_remaining"], 2
    )
    assert state["total_flexible_30_days"] == round(
        state["discretionary_before_refill"] - state["cash_cushion_refill"], 2
    )
    assert state["today_breakdown"]["total_flexible_30_days"] == state["total_flexible_30_days"]
    # Mortgage, bill, loan, card minimum, and dated miscellaneous payment all
    # reduce the same flexible pool before it reaches Today.
    assert state["today_breakdown"]["required_payments"] >= 1_325
    assert state["today_breakdown"]["normal_spending"] > 0
    assert round(sum(state[key] for key in (
        "working_safety_holdback", "cash_cushion_refill",
        "discretionary_spending_30_days", "flexible_safety_remaining",
    )), 2) == state["dated_lowest_after_normal_spending"]
    assert session.exec(select(BorrowerProfile)).first().cash_cushion_balance == 0


def test_shortfall_never_manufactures_discretionary_or_cushion_refill(session):
    today = date(2026, 9, 24)
    session.add(BorrowerProfile(
        checking_balance=100, target_buffer=500, cash_cushion_balance=0,
    ))
    session.add(BudgetCategory(name="Daily", monthly_limit=300, usual_amount=10, period="day"))
    session.add(Bill(name="Required", amount=500, due_date=today + timedelta(days=1)))
    session.commit()
    state = build_liquidity_plan(session, as_of=today)["money_state"]
    assert state["payment_plan_30_days"]["lowest_checking"] < 0
    assert state["discretionary_spending_30_days"] == 0
    assert state["total_flexible_30_days"] == 0
    assert state["cash_cushion_refill"] == 0


def test_existing_cushion_is_protected_and_missing_spending_is_not_free(session):
    today = date(2026, 9, 24)
    profile = BorrowerProfile(
        checking_balance=5_000, target_buffer=1_000, cash_cushion_balance=None,
    )
    session.add(profile)
    session.commit()
    unknown = build_liquidity_plan(session, as_of=today)["money_state"]
    assert unknown["discretionary_spending_30_days"] == 0
    assert unknown["total_flexible_30_days"] == 0
    session.add(BudgetCategory(name="Daily", monthly_limit=300, usual_amount=10, period="day"))
    session.commit()
    still_unknown = build_liquidity_plan(session, as_of=today)["money_state"]
    # The full target is reserved from checking even without a separate earmark.
    assert still_unknown["working_safety_holdback"] == 1_000
    assert still_unknown["free_to_use_now"] > 0
    profile.cash_cushion_balance = 1_000
    session.add(profile)
    session.commit()
    known = build_liquidity_plan(session, as_of=today)["money_state"]
    assert known["working_safety_holdback"] >= 1_000
    assert known["cash_cushion_refill"] == 0
    assert known["discretionary_spending_30_days"] > 0
