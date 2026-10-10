"""Acceptance tests: Flexible Today = max spend surviving all known obligations."""

from datetime import date, timedelta

import pytest

from app.core.cashflow_engine import calculate_cashflow
from app.core.income_payment import mark_income_received
from app.core.liquidity_planner import (
    FLEXIBLE_TODAY_HORIZON_DAYS,
    _collect_events,
    _simulate_waterfall,
    compute_flexible_today,
)
from app.db.models import (
    Bill,
    BorrowerProfile,
    CreditCard,
    IncomeSource,
    MiscSpending,
    MortgageAccount,
)


def _flex(session, **kwargs):
    cf = calculate_cashflow(session, **kwargs)
    return float(cf["safe_to_spend_conservative"]), cf.get("flexible_today_trace") or {}


def test_paid_today_mortgage_due_in_two_days(session):
    today = date.today()
    session.add(BorrowerProfile(checking_balance=2591.63, target_buffer=0))
    session.add(
        IncomeSource(
            source_name="Primary Job",
            amount=2230,
            pay_frequency="biweekly",
            next_pay_date=today + timedelta(days=14),
        )
    )
    session.add(
        MortgageAccount(
            lender="Citizens Bank",
            original_balance=200_000,
            current_balance=200_000,
            interest_rate=5,
            monthly_payment=1942,
            due_by_date=today + timedelta(days=2),
            recurrence="monthly",
        )
    )
    session.commit()

    flex, trace = _flex(session)
    assert flex <= 2591.63 - 1942 + 1
    assert trace["flexible_today"] == flex
    assert any(e["kind"] == "obligation" and "Citizens" in (e.get("name") or "") for e in trace["trace"]["events"])


def test_bill_due_before_next_paycheck(session):
    today = date.today()
    session.add(BorrowerProfile(checking_balance=1000, target_buffer=0))
    session.add(
        IncomeSource(
            source_name="Job",
            amount=2000,
            pay_frequency="biweekly",
            next_pay_date=today + timedelta(days=14),
        )
    )
    session.add(
        Bill(name="Water", amount=200, due_date=today + timedelta(days=3), required=True)
    )
    session.commit()

    flex, trace = _flex(session)
    assert flex <= 800 + 1
    assert trace["lowest_balance"] <= 800 + 1


def test_bill_due_after_next_paycheck_covered_by_income(session):
    today = date.today()
    session.add(BorrowerProfile(checking_balance=600, target_buffer=0))
    session.add(
        IncomeSource(
            source_name="Job",
            amount=2500,
            pay_frequency="biweekly",
            next_pay_date=today + timedelta(days=5),
        )
    )
    session.add(
        Bill(
            name="Large purchase",
            amount=1000,
            due_date=today + timedelta(days=8),
            required=True,
            recurrence="once",
        )
    )
    session.commit()

    flex, trace = _flex(session)
    assert trace["has_shortfall"] is False
    assert flex > 0


def test_large_bill_after_paycheck_zeros_flexible(session):
    """$600 now, $2k pay, $500 before pay, $2,800 after pay -> Flexible Today $0."""
    today = date.today()
    session.add(BorrowerProfile(checking_balance=600, target_buffer=0))
    session.add(
        IncomeSource(
            source_name="Job",
            amount=2000,
            pay_frequency="biweekly",
            next_pay_date=today + timedelta(days=5),
        )
    )
    session.add(
        Bill(name="Before pay", amount=500, due_date=today + timedelta(days=2), required=True)
    )
    session.add(
        Bill(
            name="After pay huge",
            amount=2800,
            due_date=today + timedelta(days=10),
            required=True,
            recurrence="once",
        )
    )
    session.commit()

    flex, trace = _flex(session)
    assert flex == 0.0
    assert trace["has_shortfall"] is True


def test_income_not_double_counted_when_marked_received(session):
    today = date.today()
    session.add(BorrowerProfile(checking_balance=2591.63, target_buffer=0))
    src = IncomeSource(
        source_name="Primary Job",
        amount=2230,
        pay_frequency="biweekly",
        next_pay_date=today,
    )
    session.add(src)
    session.add(
        MortgageAccount(
            lender="Citizens Bank",
            original_balance=200_000,
            current_balance=200_000,
            interest_rate=5,
            monthly_payment=1942,
            due_by_date=today + timedelta(days=2),
            recurrence="monthly",
        )
    )
    session.commit()
    session.refresh(src)

    before, trace_before = _flex(session)
    assert any(e["kind"] == "income" for e in trace_before["trace"]["events"])
    mark_income_received(session, src, today)
    after, trace_after = _flex(session)
    assert not any(
        e["kind"] == "income" and (e.get("date") or "")[:10] == today.isoformat()
        for e in trace_after["trace"]["events"]
    )
    assert after <= before


def test_past_due_rent_counts_immediately(session):
    today = date.today()
    if today.day <= 1:
        pytest.skip("Need today > 1st to simulate past-due rent")
    session.add(
        BorrowerProfile(
            checking_balance=2000,
            target_buffer=0,
            housing_status="rent",
            monthly_rent=1200,
            rent_due_date=today.replace(day=1),
        )
    )
    session.add(
        IncomeSource(
            source_name="Job",
            amount=3000,
            pay_frequency="monthly",
            next_pay_date=today + timedelta(days=20),
        )
    )
    session.commit()

    flex, trace = _flex(session)
    assert flex < 2000
    assert trace["has_shortfall"] is False or flex == 0


@pytest.mark.parametrize(
    "factory",
    [
        lambda t: MortgageAccount(
            lender="Bank",
            original_balance=100_000,
            current_balance=100_000,
            interest_rate=5,
            monthly_payment=800,
            due_by_date=t.replace(day=1),
            recurrence="monthly",
        ),
        lambda t: CreditCard(
            issuer="Chase",
            card_name="Card",
            balance=500,
            credit_limit=5000,
            apr=20,
            minimum_payment=40,
            due_date=t.replace(day=1),
            recurrence="monthly",
        ),
        lambda t: Bill(
            name="Water",
            amount=75,
            due_date=t.replace(day=1),
            required=True,
            recurrence="monthly",
        ),
        lambda t: MiscSpending(
            name="Fuel",
            category="other",
            amount=30,
            due_date=t.replace(day=1),
            recurrence="monthly",
        ),
        lambda t: MiscSpending(
            name="Claude",
            category="misc",
            amount=20,
            due_date=t.replace(day=1),
            recurrence="monthly",
        ),
    ],
    ids=["mortgage", "credit_card", "water_bill", "dated_spending", "misc"],
)
def test_past_due_unpaid_obligations_count_immediately(session, factory):
    today = date.today()
    if today.day <= 1:
        pytest.skip("Need today > 1st to simulate past-due anchor")
    session.add(BorrowerProfile(checking_balance=2000, target_buffer=0))
    session.add(
        IncomeSource(
            source_name="Job",
            amount=3000,
            pay_frequency="monthly",
            next_pay_date=today + timedelta(days=20),
        )
    )
    session.add(factory(today))
    session.commit()

    flex, trace = _flex(session)
    opening = trace["trace"]["events"][0]["balance_after"]
    obligations = [
        e for e in trace["trace"]["events"]
        if e["kind"] == "obligation" and (e.get("date") or "") <= today.isoformat()
    ]
    assert obligations, "past-due obligation should fire on or before today in trace"
    assert flex < opening


def test_flexible_equals_lowest_minus_buffer(session):
    """Flexible Today = max(0, lowest projected balance − buffer) when plan has no shortfall."""
    today = date.today()
    session.add(BorrowerProfile(checking_balance=1500, target_buffer=100))
    session.add(
        IncomeSource(
            source_name="Job",
            amount=2000,
            pay_frequency="biweekly",
            next_pay_date=today + timedelta(days=10),
        )
    )
    session.add(
        Bill(name="Utilities", amount=300, due_date=today + timedelta(days=5), required=True)
    )
    session.commit()

    flex, trace = _flex(session)
    inner = trace["trace"]
    assert trace["has_shortfall"] is False
    assert flex == max(0.0, round(inner["lowest_balance"] - inner["buffer"], 2))


def test_flexible_is_max_hypothetical_spend_today(session):
    """flexible today is the largest spend-at-open that keeps balance >= buffer (no buffer case)."""
    today = date.today()
    session.add(BorrowerProfile(checking_balance=1500, target_buffer=0))
    session.add(
        IncomeSource(
            source_name="Job",
            amount=2000,
            pay_frequency="biweekly",
            next_pay_date=today + timedelta(days=10),
        )
    )
    session.add(
        Bill(name="Utilities", amount=300, due_date=today + timedelta(days=5), required=True)
    )
    session.commit()

    flex, _ = _flex(session)
    through = today + timedelta(days=FLEXIBLE_TODAY_HORIZON_DAYS)
    events, _ = _collect_events(session, through)

    _, ok_steps, _ = _simulate_waterfall(1500 - flex, events, through=through)
    ok_shortfall = any(s.get("action") == "shortfall" for s in ok_steps)

    _, tight_steps, _ = _simulate_waterfall(max(0.0, 1500 - flex - 0.01), events, through=through)
    tight_shortfall = any(s.get("action") == "shortfall" for s in tight_steps)

    if flex > 0:
        assert not ok_shortfall
        assert tight_shortfall


def test_shortfall_causes_zero_flexible(session):
    today = date.today()
    session.add(BorrowerProfile(checking_balance=100, target_buffer=0))
    session.add(
        Bill(name="Huge", amount=5000, due_date=today + timedelta(days=1), required=True)
    )
    session.commit()

    flex, trace = _flex(session)
    assert flex == 0.0
    assert trace["has_shortfall"] is True


def test_trace_includes_waterfall_fields(session):
    today = date.today()
    session.add(BorrowerProfile(checking_balance=500, target_buffer=50))
    session.add(
        Bill(name="Rent", amount=400, due_date=today + timedelta(days=4), required=True)
    )
    session.commit()

    _, trace = _flex(session)
    inner = trace["trace"]
    assert inner["starting_balance"] == 500
    assert inner["buffer"] == 50
    assert "flexible_today" in inner
    assert inner["events"][0]["kind"] == "opening"
    assert inner["same_day_order"] == "income_before_obligations"


def test_compute_flexible_today_matches_cashflow(session):
    today = date.today()
    session.add(BorrowerProfile(checking_balance=823, target_buffer=0))
    session.add(
        IncomeSource(
            source_name="Job",
            amount=2230,
            pay_frequency="biweekly",
            next_pay_date=today + timedelta(days=3),
        )
    )
    session.commit()

    through = today + timedelta(days=FLEXIBLE_TODAY_HORIZON_DAYS)
    events, _ = _collect_events(session, through)
    direct = compute_flexible_today(starting_cash=823, buffer=0, events=events)
    cf = calculate_cashflow(session)
    assert cf["safe_to_spend_conservative"] == direct["flexible_today"]
