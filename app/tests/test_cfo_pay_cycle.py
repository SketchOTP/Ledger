"""Pay-cycle CFO safe-to-spend — forward waterfall through each pay period."""

from datetime import date, timedelta

from app.core.cashflow_engine import calculate_cashflow
from app.core.income_payment import mark_income_received
from app.db.models import (
    Bill,
    BorrowerProfile,
    CreditCard,
    IncomeSource,
    MortgageAccount,
)


def test_flexible_today_uses_pay_cycle_waterfall(session):
    """Home 'flexible today' must follow pay-cycle obligation waterfall, not a flat reserve."""
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
        Bill(name="Water", amount=75, due_date=today + timedelta(days=2), required=True)
    )
    session.add(
        CreditCard(
            issuer="Chase",
            card_name="Freedom",
            balance=500,
            credit_limit=5000,
            apr=24.9,
            minimum_payment=25,
            due_date=today + timedelta(days=4),
            recurrence="monthly",
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

    cf = calculate_cashflow(session)
    cash = calculate_cashflow(session)
    flexible = float(cash["safe_to_spend_conservative"])
    trace = cf.get("flexible_today_trace") or {}

    assert flexible <= 2591.63 - 1942 - 25 - 75 + 1
    assert flexible == float(trace.get("flexible_today", flexible))
    assert trace.get("horizon_days") == 180


def test_pay_cycle_zero_when_remaining_cannot_cover_later_bills(session):
    """If only $600 remains after early bills but later cycle bills exceed that, flexible is $0."""
    today = date.today()
    session.add(BorrowerProfile(checking_balance=2600, target_buffer=0))
    session.add(
        IncomeSource(
            source_name="Job",
            amount=500,
            pay_frequency="biweekly",
            next_pay_date=today + timedelta(days=20),
        )
    )
    session.add(
        MortgageAccount(
            lender="Bank",
            original_balance=100_000,
            current_balance=100_000,
            interest_rate=5,
            monthly_payment=2000,
            due_by_date=today + timedelta(days=2),
            recurrence="monthly",
        )
    )
    session.add(
        Bill(
            name="Large bill",
            amount=900,
            due_date=today + timedelta(days=10),
            required=True,
            recurrence="monthly",
        )
    )
    session.commit()

    cf = calculate_cashflow(session)
    # 2600 - 2000 = 600 left, but 900 due before next paycheck with no income -> shortfall
    assert cf["flexible_today_trace"]["has_shortfall"] is True
    assert cf["safe_to_spend_conservative"] == 0.0


def test_pay_cycle_counts_paycheck_before_large_bill(session):
    """A large bill after the next paycheck can be covered by that income in the waterfall."""
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

    cf = calculate_cashflow(session)
    assert cf["flexible_today_trace"]["has_shortfall"] is False
    assert cf["safe_to_spend_conservative"] > 0


def test_received_paycheck_not_double_counted_in_waterfall(session):
    """Marking today's paycheck received removes it from the plan when checking already includes it."""
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

    before = calculate_cashflow(session)
    before_flexible = float(before["safe_to_spend_conservative"])
    mark_income_received(session, src, today)
    after = calculate_cashflow(session)

    trace_before = (before.get("flexible_today_trace") or {}).get("trace") or {}
    trace_after = (after.get("flexible_today_trace") or {}).get("trace") or {}
    income_today_before = [
        e for e in trace_before.get("events") or []
        if e.get("kind") == "income" and (e.get("date") or "")[:10] == today.isoformat()
    ]
    income_today_after = [
        e for e in trace_after.get("events") or []
        if e.get("kind") == "income" and (e.get("date") or "")[:10] == today.isoformat()
    ]
    assert income_today_before
    assert not income_today_after
    assert float(after["safe_to_spend_conservative"]) < before_flexible
    assert after["safe_to_spend_conservative"] <= 2591.63 - 1942 + 1
