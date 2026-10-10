"""Synthetic proof that cash rescue stays conditional and protects living costs."""

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.deps import get_db
from app.core.budget_engine import (
    planned_spending_daily_events,
    planned_spending_in_window,
    remaining_monthly_budget,
)
from app.core.cash_rescue import build_cash_rescue_plan
from app.core.liquidity_planner import build_liquidity_plan
from app.core.misc_schedule import monthly_misc_total
from app.core.v2_today import build_today
from app.db.models import (
    Bill,
    BorrowerProfile,
    BudgetCategory,
    IncomeSource,
    MiscSpending,
    Transaction,
)
from app.main import app


def _event(day: date, kind: str, amount: float, name: str, category: str) -> dict:
    return {
        "date": day.isoformat(), "kind": kind, "amount": amount,
        "name": name, "category": category,
    }


def test_single_payment_shift_is_hypothetical_and_does_not_hide_remaining_gap():
    today = date(2026, 9, 1)
    original = [
        _event(today + timedelta(days=1), "obligation", 40, "Small payment", "misc"),
        _event(today + timedelta(days=2), "obligation", 220, "Housing", "mortgage"),
        _event(today + timedelta(days=4), "income", 200, "Pay", "income"),
    ]

    plan = build_cash_rescue_plan(
        cash=200, events=original, as_of=today, normal_spending_reserve=0,
        normal_spending_entered=False, cushion_target=0,
    )

    assert plan["first_gap"] == {
        "date": "2026-09-03", "payment": "Housing", "amount": 60,
    }
    assert plan["needed_before_income"] == 60
    assert plan["conditional_only"] is True
    assert original[1]["date"] == "2026-09-03"  # No due-date mutation.
    by_name = {row["payment"]: row for row in plan["scenarios"]}
    assert by_name["Small payment"]["remaining_gap_before_income"] == 20
    assert by_name["Small payment"]["closes_recorded_gap"] is False
    assert by_name["Housing"]["remaining_gap_before_income"] == 0
    assert by_name["Housing"]["requires_payee_confirmation"] is True


def test_everyday_spending_is_held_before_claiming_a_bridge():
    today = date(2026, 9, 1)
    events = [
        _event(today + timedelta(days=2), "obligation", 220, "Housing", "mortgage"),
        _event(today + timedelta(days=4), "income", 200, "Pay", "income"),
    ]
    without = build_cash_rescue_plan(
        cash=200, events=events, as_of=today, normal_spending_reserve=0,
        normal_spending_entered=False, cushion_target=0,
    )
    with_living = build_cash_rescue_plan(
        cash=200, events=events, as_of=today, normal_spending_reserve=50,
        normal_spending_entered=True, cushion_target=25,
    )
    assert without["needed_before_income"] == 20
    assert with_living["needed_before_income"] == 70
    assert with_living["normal_spending_held"] == 50
    assert with_living["cushion_gap"] > 0
    assert with_living["gap_depends_on_living_holdback"] is False


def test_bill_only_coverage_is_not_claimed_as_full_living_coverage():
    today = date(2026, 9, 1)
    plan = build_cash_rescue_plan(
        cash=200,
        events=[_event(today + timedelta(days=2), "obligation", 180, "Rent", "rent")],
        as_of=today, normal_spending_reserve=50,
        normal_spending_entered=True, cushion_target=0,
    )
    assert plan["first_gap"]["amount"] == 30
    assert plan["gap_depends_on_living_holdback"] is True


def test_two_confirmed_changes_may_be_needed_to_bridge_one_paycheck():
    today = date(2026, 9, 1)
    plan = build_cash_rescue_plan(
        cash=30,
        events=[
            _event(today + timedelta(days=1), "obligation", 50, "Payment A", "bill"),
            _event(today + timedelta(days=2), "obligation", 50, "Payment B", "bill"),
            _event(today + timedelta(days=4), "income", 200, "Pay", "income"),
        ],
        as_of=today, normal_spending_reserve=0,
        normal_spending_entered=True, cushion_target=0,
    )
    assert plan["needed_before_income"] == 70
    pair = next(row for row in plan["scenarios"] if len(row["changes"]) == 2)
    assert pair["remaining_gap_before_income"] == 0
    assert pair["closes_recorded_gap"] is True
    assert len(pair["changes"]) == 2


def test_no_recorded_income_does_not_invent_a_payment_extension():
    today = date(2026, 9, 1)
    plan = build_cash_rescue_plan(
        cash=50,
        events=[_event(today + timedelta(days=2), "obligation", 100, "Bill", "bill")],
        as_of=today, normal_spending_reserve=0, normal_spending_entered=False,
        cushion_target=0,
    )
    assert plan["needed_before_income"] == 50
    assert plan["next_income"] is None
    assert plan["scenarios"] == []


def test_covered_bills_can_still_leave_cushion_below_target():
    today = date(2026, 9, 1)
    plan = build_cash_rescue_plan(
        cash=500,
        events=[_event(today + timedelta(days=2), "obligation", 300, "Bill", "bill")],
        as_of=today, normal_spending_reserve=0, normal_spending_entered=True,
        cushion_target=250,
    )
    assert plan["first_gap"] is None
    assert plan["cushion_gap"] == 50


def test_budget_uses_only_current_positive_category_outflows(session):
    today = date.today()
    session.add(BudgetCategory(name="Groceries", monthly_limit=100))
    session.add(Transaction(
        transaction_date=today, amount=25, category="groceries", description="test",
    ))
    session.add(Transaction(
        transaction_date=today, amount=-10, category="groceries", description="refund",
    ))
    session.add(Transaction(
        transaction_date=today - timedelta(days=40), amount=90,
        category="groceries", description="prior month",
    ))
    session.add(Transaction(
        transaction_date=today, amount=1000, category="housing", description="bill",
    ))
    session.commit()

    assert remaining_monthly_budget(session, today) == 75
    state = build_liquidity_plan(session, starting_cash=500)["money_state"]
    assert state["expected_variable_reserve"] == planned_spending_in_window(
        session, today, today + timedelta(days=30)
    )
    assert state["cash_rescue"]["normal_spending_entered"] is True
    assert state["cash_rescue"]["cushion_target"] == state["cash_cushion_goal"]
    assert state["safety_cushion"] >= state["cash_cushion_goal"]


def test_rolling_living_plan_includes_part_of_next_month(session):
    session.add(BudgetCategory(name="Groceries", monthly_limit=310))
    session.commit()
    assert planned_spending_in_window(
        session, date(2026, 9, 23), date(2026, 10, 23)
    ) == 540


def test_living_costs_accrue_by_date_instead_of_all_before_payday(session):
    today = date(2026, 9, 23)
    session.add(BudgetCategory(name="Groceries", monthly_limit=120))
    session.commit()
    schedule = planned_spending_daily_events(session, today, today + timedelta(days=30))
    assert sum(row["amount"] for row in schedule if row["date"] <= "2026-09-26") == 60
    assert abs(sum(row["amount"] for row in schedule) - planned_spending_in_window(
        session, today, today + timedelta(days=30)
    )) <= 0.01

    plan = build_cash_rescue_plan(
        cash=200,
        events=[
            _event(today + timedelta(days=2), "obligation", 250, "Bill", "bill"),
            _event(today + timedelta(days=4), "income", 300, "Pay", "income"),
        ],
        as_of=today, normal_spending_reserve=209.03,
        normal_spending_schedule=schedule, normal_spending_entered=True,
        cushion_target=0,
    )
    assert plan["needed_before_income"] == 110
    assert plan["scenarios"][0]["remaining_gap_before_income"] == 0
    assert all(
        change["payment"] != "Everyday spending"
        for option in plan["scenarios"] for change in option["changes"]
    )


def test_today_exposes_conditional_gap_without_changing_owner_data(session):
    today = date.today()
    session.add(BorrowerProfile(checking_balance=200, target_buffer=0))
    bill = Bill(name="Housing", amount=250, due_date=today + timedelta(days=2), required=True)
    session.add(bill)
    session.add(IncomeSource(
        source_name="Pay", amount=300, pay_frequency="biweekly",
        next_pay_date=today + timedelta(days=4),
    ))
    session.commit()

    rescue = build_today(session)["financial_state"]["cash_rescue"]
    session.refresh(bill)
    assert rescue["needed_before_income"] == 50
    assert rescue["normal_spending_entered"] is False
    assert rescue["scenarios"][0]["requires_payee_confirmation"] is True
    assert bill.due_date == today + timedelta(days=2)


def test_exact_zero_lowest_balance_is_not_replaced_by_starting_cash(session):
    today = date.today()
    session.add(BorrowerProfile(checking_balance=100, target_buffer=0))
    session.add(Bill(
        name="Exact balance payment", amount=100,
        due_date=today + timedelta(days=1), required=True,
    ))
    session.commit()

    plan = build_liquidity_plan(session, starting_cash=100)
    assert plan["money_state"]["today_breakdown"]["lowest_balance_30_days"] == 0
    assert plan["money_state"]["known_obligation_reserve"] == 100


def test_annual_and_quarterly_misc_spending_are_not_monthly(session):
    session.add(MiscSpending(name="Holiday", category="other", amount=120, recurrence="annual"))
    session.add(MiscSpending(name="Seasonal", category="other", amount=90, recurrence="quarterly"))
    session.commit()
    assert monthly_misc_total(session) == 40


def test_dated_misc_stays_in_scheduled_payments_not_an_unknown_optional_bucket(session):
    today = date.today()
    session.add(BorrowerProfile(checking_balance=200))
    session.add(MiscSpending(
        name="Scheduled payment", category="other", amount=40,
        due_date=today + timedelta(days=2), recurrence="once",
    ))
    session.commit()
    breakdown = build_liquidity_plan(session, starting_cash=200)["money_state"]["today_breakdown"]
    assert breakdown["required_payments"] == 40
    assert breakdown["normal_spending"] == 0


def test_owner_can_add_edit_and_remove_everyday_spending_through_api():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(BorrowerProfile(checking_balance=500))
        session.commit()
        app.dependency_overrides[get_db] = lambda: session
        client = TestClient(app)
        try:
            created = client.post(
                "/api/spending/budget", json={"name": "Groceries", "monthly_limit": 120},
            )
            assert created.status_code == 200
            assert "id" in created.json(), created.json()
            budget_id = created.json()["id"]
            assert client.get("/api/v2/today").json()["financial_state"]["cash_rescue"]["normal_spending_entered"]

            edited = client.patch(
                f"/api/spending/budget/{budget_id}",
                json={"name": "Groceries", "monthly_limit": 180},
            )
            assert edited.status_code == 200
            assert edited.json()["monthly_limit"] == 180
            assert remaining_monthly_budget(session, date.today()) == 180

            assert client.delete(f"/api/spending/budget/{budget_id}").status_code == 200
            assert client.get("/api/spending/budget").json() == []
            assert not client.get("/api/v2/today").json()["financial_state"]["cash_rescue"]["normal_spending_entered"]
        finally:
            app.dependency_overrides.pop(get_db, None)
