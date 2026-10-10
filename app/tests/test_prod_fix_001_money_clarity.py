"""Production Fix 001: Today money clarity and reserve reconciliation."""

from datetime import date, timedelta

from app.core.liquidity_planner import build_liquidity_plan
from app.core.v2_today import build_today
from app.db.models import Bill, BorrowerProfile, IncomeSource


def test_reserve_components_reconcile_without_obligation_double_counting(session):
    today = date.today()
    session.add(BorrowerProfile(checking_balance=2_000, target_buffer=500))
    session.add(
        IncomeSource(
            source_name="Pay",
            amount=3_000,
            pay_frequency="monthly",
            next_pay_date=today + timedelta(days=7),
        )
    )
    session.add(
        Bill(
            name="Rent",
            amount=1_000,
            due_date=today + timedelta(days=10),
            required=True,
            recurrence="monthly",
        )
    )
    session.commit()

    plan = build_liquidity_plan(session)
    state = plan["money_state"]
    breakdown = state["protected_cash_breakdown"]

    assert breakdown["total"] == state["protected_cash"]
    assert sum(breakdown[key] for key in ("for_bills", "for_normal_spending", "safety_cushion")) == state[
        "protected_cash"
    ]
    # The emergency target is a cushion target, not the same 30-day obligation
    # total that is already simulated by the literal-date waterfall.
    assert state["emergency_reserve_target"] <= state["safety_cushion"]
    assert state["emergency_reserve_target"] < plan["horizons"][30]["obligations_scheduled"]


def test_checking_only_household_can_cover_emergency_cushion_without_savings(session):
    session.add(BorrowerProfile(checking_balance=5_000, target_buffer=500))
    session.commit()

    state = build_liquidity_plan(session)["money_state"]

    assert state["emergency_reserve_account_balance"] == 0
    assert state["emergency_reserve_balance"] > 0
    assert state["emergency_reserve_shortfall"] == 0


def test_today_exposes_coverage_and_money_breakdown(session):
    today = date.today()
    session.add(BorrowerProfile(checking_balance=1_000, target_buffer=100))
    session.add(
        IncomeSource(
            source_name="Pay",
            amount=2_000,
            pay_frequency="biweekly",
            next_pay_date=today + timedelta(days=5),
        )
    )
    session.add(
        Bill(name="Rent", amount=700, due_date=today + timedelta(days=8), required=True)
    )
    session.commit()

    today_payload = build_today(session)
    breakdown = today_payload["financial_state"]["today_breakdown"]

    assert breakdown["coverage_status"] == "covered"
    assert breakdown["coverage_message"] == "All known bills are covered."
    assert breakdown["required_payments"] > 0
    assert breakdown["available_now"] == 1_000
    assert breakdown["extra_reason"]


def test_missing_due_date_prevents_false_bill_coverage(session):
    session.add(BorrowerProfile(checking_balance=5_000, target_buffer=0))
    session.add(Bill(name="Undated required", amount=700, required=True))
    session.commit()

    breakdown = build_today(session)["financial_state"]["today_breakdown"]

    assert breakdown["coverage_status"] == "unknown"
    assert "due date" in breakdown["coverage_message"]
    assert breakdown["missing_due_dates"]


def test_today_explains_a_real_required_payment_shortfall(session):
    today = date.today()
    session.add(BorrowerProfile(checking_balance=200, target_buffer=0))
    session.add(
        IncomeSource(
            source_name="Pay",
            amount=100,
            pay_frequency="monthly",
            next_pay_date=today + timedelta(days=14),
        )
    )
    session.add(Bill(name="Rent", amount=1_000, due_date=today + timedelta(days=3), required=True))
    session.commit()

    breakdown = build_today(session)["financial_state"]["today_breakdown"]

    assert breakdown["coverage_status"] == "shortfall"
    assert "short" in breakdown["coverage_message"]
    assert breakdown["extra_spending"] == 0
