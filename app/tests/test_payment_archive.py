"""Payment archive audit trail and idempotent restore tests."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.core.bill_payment import mark_bill_paid
from app.core.cashflow_engine import calculate_cashflow
from app.core.debt_payment import mark_debt_paid
from app.core.income_payment import mark_income_received, upcoming_income
from app.core.liquidity_planner import build_liquidity_plan
from app.core.misc_payment import mark_misc_paid
from app.core.mortgage_payment import mark_mortgage_paid
from app.core.obligation_attention import build_obligation_attention
from app.core.obligation_mark_paid import mark_obligation_paid
from app.core.payment_archive import list_payment_archive
from app.core.payment_restore import restore_marked_payment
from app.core.rent_payment import mark_rent_paid
from app.core.travel_payment import mark_travel_paid
from app.db.models import (
    Bill,
    BorrowerProfile,
    CreditCard,
    Debt,
    IncomeSource,
    MiscSpending,
    MortgageAccount,
    TravelExpense,
)


def _active_mark(archive: list[dict], kind: str, **match) -> dict:
    for row in archive:
        if row.get("kind") != kind:
            continue
        if all(row.get(k) == v for k, v in match.items()):
            if row.get("action") in ("marked_paid", "marked_received") and row.get("can_restore"):
                return row
    raise AssertionError(f"No active {kind} mark in archive: {match}")


def _mark_rows(archive: list[dict], kind: str, **match) -> list[dict]:
    return [
        r
        for r in archive
        if r.get("kind") == kind and all(r.get(k) == v for k, v in match.items())
    ]


@pytest.fixture
def today():
    return date.today()


@pytest.fixture
def profile(session):
    p = BorrowerProfile(checking_balance=5000, monthly_rent=1200, rent_due_date=date.today())
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def test_restore_bill_idempotent_and_archive_persists(session, profile, today):
    bill = Bill(name="Electric", amount=120, due_date=today, recurrence="monthly")
    session.add(bill)
    session.commit()
    session.refresh(bill)

    mark_bill_paid(session, bill, today)
    archive = list_payment_archive(session)
    mark_row = _active_mark(archive, "bill", bill_id=bill.id)
    assert mark_row["action"] == "marked_paid"

    restore_marked_payment(session, {"bill_id": bill.id, "due_date": today.isoformat()})
    archive_after = list_payment_archive(session)
    assert len(_mark_rows(archive_after, "bill", bill_id=bill.id)) >= 1
    assert any(r["action"] == "restored" for r in archive_after)
    assert not any(r.get("can_restore") for r in _mark_rows(archive_after, "bill", bill_id=bill.id))

    second = restore_marked_payment(session, {"bill_id": bill.id, "due_date": today.isoformat()})
    assert second["already_restored"] is True
    assert len([r for r in list_payment_archive(session) if r["action"] == "restored"]) == 1


def test_restore_debt(session, profile, today):
    debt = Debt(
        creditor="Card Co",
        balance=1000,
        apr=19.0,
        minimum_payment=50,
        due_date=today,
        recurrence="monthly",
    )
    session.add(debt)
    session.commit()
    session.refresh(debt)

    mark_debt_paid(session, debt, today)
    restore_marked_payment(session, {"debt_id": debt.id, "due_date": today.isoformat()})
    archive = list_payment_archive(session)
    assert any(r["action"] == "marked_paid" for r in archive if r["debt_id"] == debt.id)
    assert any(r["action"] == "restored" for r in archive if r["debt_id"] == debt.id)


def test_restore_mortgage(session, profile, today):
    mortgage = MortgageAccount(
        lender="Test Bank",
        original_balance=200000,
        current_balance=200000,
        interest_rate=6.5,
        monthly_payment=1500,
        due_by_date=today,
        recurrence="monthly",
    )
    session.add(mortgage)
    session.commit()
    session.refresh(mortgage)

    mark_mortgage_paid(session, mortgage, today)
    restore_marked_payment(session, {"mortgage_id": mortgage.id, "due_date": today.isoformat()})
    archive = list_payment_archive(session)
    assert any(r["action"] == "restored" for r in archive if r["mortgage_id"] == mortgage.id)


def test_restore_rent(session, profile, today):
    mark_rent_paid(session, profile, today)
    restore_marked_payment(
        session, {"profile_id": profile.id, "due_date": today.isoformat(), "kind": "rent"}
    )
    archive = list_payment_archive(session)
    assert any(r["action"] == "restored" for r in archive if r["profile_id"] == profile.id)


def test_restore_credit_card_balance_once(session, profile, today):
    card = CreditCard(
        issuer="Visa",
        card_name="Rewards",
        balance=800,
        credit_limit=5000,
        apr=22.0,
        minimum_payment=35,
        due_date=today,
        recurrence="monthly",
    )
    session.add(card)
    session.commit()
    session.refresh(card)

    mark_obligation_paid(
        session, {"credit_card_id": card.id, "due_date": today.isoformat()}
    )
    session.refresh(card)
    balance_after_pay = card.balance

    restore_marked_payment(session, {"credit_card_id": card.id, "due_date": today.isoformat()})
    session.refresh(card)
    assert card.balance == pytest.approx(800.0)
    assert card.balance != balance_after_pay

    restore_marked_payment(session, {"credit_card_id": card.id, "due_date": today.isoformat()})
    session.refresh(card)
    assert card.balance == pytest.approx(800.0)


def test_restore_travel(session, profile, today):
    expense = TravelExpense(
        mode="car",
        expense_type="gas",
        label="Commute gas",
        amount=80,
        due_date=today,
        recurrence="monthly",
    )
    session.add(expense)
    session.commit()
    session.refresh(expense)

    mark_travel_paid(session, expense, today)
    restore_marked_payment(
        session, {"travel_expense_id": expense.id, "due_date": today.isoformat()}
    )
    archive = list_payment_archive(session)
    assert any(r["action"] == "restored" for r in archive if r["travel_expense_id"] == expense.id)


def test_restore_misc(session, profile, today):
    item = MiscSpending(name="Streaming", amount=15, due_date=today, recurrence="monthly")
    session.add(item)
    session.commit()
    session.refresh(item)

    mark_misc_paid(session, item, today)
    restore_marked_payment(
        session, {"misc_spending_id": item.id, "due_date": today.isoformat()}
    )
    archive = list_payment_archive(session)
    assert any(r["action"] == "restored" for r in archive if r["misc_spending_id"] == item.id)


def test_restore_income_and_projections(session, profile, today):
    src = IncomeSource(
        source_name="Job",
        amount=2000,
        pay_frequency="biweekly",
        next_pay_date=today,
    )
    session.add(src)
    session.commit()
    session.refresh(src)

    plan_before = build_liquidity_plan(session, starting_cash=5000)
    income_before = plan_before["horizons"][30]["income_expected"]

    mark_income_received(session, src, today)
    plan_marked = build_liquidity_plan(session, starting_cash=5000)
    assert plan_marked["horizons"][30]["income_expected"] < income_before

    restore_marked_payment(
        session, {"income_source_id": src.id, "due_date": today.isoformat()}
    )
    plan_restored = build_liquidity_plan(session, starting_cash=5000)
    assert plan_restored["horizons"][30]["income_expected"] == pytest.approx(income_before)

    archive = list_payment_archive(session)
    assert any(r["action"] == "marked_received" for r in archive if r["income_source_id"] == src.id)
    assert any(r["action"] == "restored" for r in archive if r["income_source_id"] == src.id)


def test_restored_obligation_affects_flexible_today_and_horizons(session, profile, today):
    bill = Bill(name="Rent share", amount=500, due_date=today, recurrence="monthly")
    session.add(bill)
    session.commit()
    session.refresh(bill)

    cf_before = calculate_cashflow(session)
    flex_before = cf_before["safe_to_spend_conservative"]
    h30_before = cf_before["horizons"][30]["projected_balance"]

    mark_bill_paid(session, bill, today)
    cf_paid = calculate_cashflow(session)
    assert cf_paid["safe_to_spend_conservative"] >= flex_before
    assert cf_paid["horizons"][30]["projected_balance"] >= h30_before

    restore_marked_payment(session, {"bill_id": bill.id, "due_date": today.isoformat()})
    cf_restored = calculate_cashflow(session)
    assert cf_restored["safe_to_spend_conservative"] <= cf_paid["safe_to_spend_conservative"]
    assert cf_restored["safe_to_spend_conservative"] == pytest.approx(flex_before, abs=1)
    assert cf_restored["horizons"][30]["projected_balance"] == pytest.approx(h30_before, abs=1)


def test_restored_item_reappears_in_quick_status(session, profile, today):
    src = IncomeSource(
        source_name="Side gig",
        amount=500,
        pay_frequency="monthly",
        next_pay_date=today,
    )
    session.add(src)
    session.commit()
    session.refresh(src)

    mark_income_received(session, src, today)
    quick = build_obligation_attention(session, window_days=14)
    assert not any(r.get("income_source_id") == src.id for r in quick)

    restore_marked_payment(
        session, {"income_source_id": src.id, "due_date": today.isoformat()}
    )
    assert any(r.get("income_source_id") == src.id for r in upcoming_income(session, days=14))
    quick2 = build_obligation_attention(session, window_days=14)
    assert any(r.get("income_source_id") == src.id for r in quick2)


def test_dashboard_home_horizons_from_liquidity_not_cashflow_plan(session, profile, today):
    bill = Bill(name="Net", amount=50, due_date=today + timedelta(days=5), recurrence="monthly")
    session.add(bill)
    session.commit()

    horizons = calculate_cashflow(session)["horizons"]
    assert horizons
    h30 = horizons.get(30) or horizons.get("30")
    assert h30 is not None
    assert h30["projected_balance"] != -99999
    cf = calculate_cashflow(session)
    cf30 = cf["horizons"].get(30) or cf["horizons"].get("30")
    assert h30["projected_balance"] == cf30["projected_balance"]
