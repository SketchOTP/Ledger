"""Income mark-received tests."""

from datetime import date, timedelta

from app.core.account_balances import account_totals
from app.core.cashflow_engine import calculate_cashflow
from app.core.income_payment import mark_income_received, upcoming_income
from app.core.liquidity_planner import build_liquidity_plan
from app.core.obligation_attention import build_obligation_attention
from app.db.models import BorrowerProfile, IncomeSource


def test_mark_received_advances_pay_date_without_crediting_checking(session):
    today = date.today()
    session.add(BorrowerProfile(checking_balance=500))
    src = IncomeSource(
        source_name="Job",
        amount=2000,
        pay_frequency="biweekly",
        next_pay_date=today,
    )
    session.add(src)
    session.commit()
    session.refresh(src)

    paycheck, next_pay = mark_income_received(session, src, today)
    assert paycheck.amount == 2000
    assert next_pay == today + timedelta(days=14)
    assert account_totals(session)["checking"] == 500


def test_received_income_removed_from_quick_status_and_timeline(session):
    today = date.today()
    session.add(BorrowerProfile(checking_balance=100))
    src = IncomeSource(
        source_name="Job",
        amount=1500,
        pay_frequency="biweekly",
        next_pay_date=today,
    )
    session.add(src)
    session.commit()
    session.refresh(src)

    before = upcoming_income(session, days=14)
    assert any(r["income_source_id"] == src.id for r in before)

    mark_income_received(session, src, today)

    after_income = upcoming_income(session, days=14)
    assert not any(r["due_date"] == today.isoformat() for r in after_income)

    quick = build_obligation_attention(session, window_days=14)
    assert not any(
        item.get("kind") == "income" and item.get("due_date", "")[:10] == today.isoformat()
        for item in quick
    )

    plan = build_liquidity_plan(session)
    playbook = plan.get("playbook") or []
    assert not any(
        step.get("action") in ("receive_income", "deposit_to_checking")
        and step.get("date", "")[:10] == today.isoformat()
        for step in playbook
    )


def test_cashflow_counts_received_income(session):
    today = date.today()
    session.add(BorrowerProfile(checking_balance=800))
    src = IncomeSource(
        source_name="Job",
        amount=1200,
        pay_frequency="biweekly",
        next_pay_date=today,
    )
    session.add(src)
    session.commit()
    session.refresh(src)

    mark_income_received(session, src, today)
    cf = calculate_cashflow(session)
    assert cf["income_already_received"] >= 1200
    assert cf["checking_balance"] == 800
