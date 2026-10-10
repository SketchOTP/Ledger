"""Cashflow engine tests."""

from datetime import date, timedelta

from app.core.cashflow_engine import calculate_cashflow
from app.db.models import Bill, BorrowerProfile, IncomeSource, MortgageAccount


def test_cashflow_safe_surplus(session, sample_data):
    cf = calculate_cashflow(session, starting_cash=5000)
    assert cf["risk_level"] in ("safe", "tight", "danger")
    assert cf["safe_to_spend"] >= 0


def test_cashflow_shortfall(session):
    today = date.today()
    session.add(
        IncomeSource(
            source_name="Low",
            amount=100,
            pay_frequency="monthly",
            next_pay_date=today + timedelta(days=14),
        )
    )
    session.add(Bill(name="Big", amount=5000, required=True, due_date=today + timedelta(days=5)))
    session.commit()
    cf = calculate_cashflow(session, starting_cash=50)
    assert cf["risk_level"] in ("danger", "tight")
    assert cf["projected_end_balance"] < 0 or cf["risk_level"] == "danger"


def test_bill_due_before_paycheck(session):
    today = date.today()
    session.add(
        IncomeSource(
            source_name="Job",
            amount=2000,
            next_pay_date=today + timedelta(days=14),
        )
    )
    session.add(
        Bill(name="Early", amount=500, due_date=today + timedelta(days=2), required=True)
    )
    session.commit()
    cf = calculate_cashflow(session, starting_cash=100)
    assert cf["required_bills"] >= 500


def test_safe_to_spend_excludes_future_paycheck(session):
    """Safe 'now' must not add income that has not arrived yet."""
    today = date.today()
    session.add(
        IncomeSource(
            source_name="Job",
            amount=2203,
            next_pay_date=today + timedelta(days=14),
        )
    )
    session.commit()
    cf = calculate_cashflow(session, starting_cash=56)
    assert cf["safe_to_spend_conservative"] < 56
    assert cf["safe_to_spend"] != 56 + 2203


def test_risk_skips_low_buffer_when_target_zero(session):
    from app.core.risk_engine import scan_risks

    session.add(
        BorrowerProfile(
            checking_balance=100,
            target_buffer=0,
            emergency_fund_balance=0,
        )
    )
    session.commit()
    codes = {r["code"] for r in scan_risks(session)}
    assert "low_emergency_buffer" not in codes


def test_no_buffer_when_profile_target_zero(session):
    today = date.today()
    session.add(
        BorrowerProfile(checking_balance=1500, target_buffer=0),
    )
    session.add(
        IncomeSource(
            source_name="Job",
            amount=2000,
            next_pay_date=today + timedelta(days=14),
        )
    )
    session.commit()
    cf = calculate_cashflow(session)
    assert cf["minimum_buffer"] == 0
    assert "keeping $500" not in cf.get("headline", "")
    assert "keeping $500" not in (cf.get("liquidity_plan") or {}).get("headline", "")


def test_safe_to_spend_reserves_bills_before_pay(session):
    today = date.today()
    session.add(
        IncomeSource(
            source_name="Job",
            amount=2000,
            next_pay_date=today + timedelta(days=14),
        )
    )
    session.add(Bill(name="Rent", amount=500, due_date=today + timedelta(days=3), required=True))
    session.commit()
    cf = calculate_cashflow(session, starting_cash=600)
    # 600 - 500 bill - 500 buffer = 0 (not 600+2000)
    assert cf["committed_until_pay"] >= 500
    assert cf["flexible_before_next_income"] <= 100


def test_safe_to_spend_zero_when_short(session):
    today = date.today()
    session.add(
        IncomeSource(
            source_name="Job",
            amount=100,
            next_pay_date=today + timedelta(days=14),
        )
    )
    session.add(Bill(name="Due", amount=800, due_date=today + timedelta(days=2), required=True))
    session.commit()
    cf = calculate_cashflow(session, starting_cash=200)
    assert cf["safe_to_spend"] == 0
    assert cf["risk_level"] == "danger"


def test_safe_to_spend_counts_overdue_debt(session):
    """Unpaid debt due before today must reduce safe to spend."""
    today = date.today()
    from app.db.models import Debt

    session.add(
        IncomeSource(
            source_name="Job",
            amount=2000,
            pay_frequency="biweekly",
            next_pay_date=today + timedelta(days=14),
        )
    )
    session.add(
        Debt(
            creditor="53rd Bank",
            balance=5000,
            apr=17,
            minimum_payment=255,
            due_date=today - timedelta(days=1),
            recurrence="monthly",
        )
    )
    session.commit()
    cf = calculate_cashflow(session, starting_cash=1340)
    assert cf["debt_due_this_period"] >= 255
    playbook = cf.get("playbook") or []
    assert any(
        s.get("action") == "pay_from_checking" and s.get("amount") == 255 for s in playbook
    )


def test_safe_to_spend_counts_overdue_mortgage(session):
    """Unpaid mortgage due before today must reduce safe to spend."""
    today = date.today()
    session.add(
        BorrowerProfile(checking_balance=2591.63, target_buffer=0),
    )
    session.add(
        IncomeSource(
            source_name="Job",
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
            due_by_date=today.replace(day=1),
            recurrence="monthly",
        )
    )
    session.commit()
    cf = calculate_cashflow(session)
    assert cf["mortgage_due_this_period"] >= 1942
    assert cf["cycle_obligations"] >= 1942
    assert cf["safe_to_spend_conservative"] <= 2591.63 - 1942 + 1


def test_safe_to_spend_pay_cycle_includes_post_paycheck_bills(session):
    """Bills due after next paycheck but before the following one reserve cash now."""
    today = date.today()
    session.add(
        IncomeSource(
            source_name="Primary Job",
            amount=2230,
            pay_frequency="biweekly",
            next_pay_date=today + timedelta(days=8),
        )
    )
    session.add(
        Bill(name="Before pay", amount=200, due_date=today + timedelta(days=2), required=True)
    )
    session.add(
        Bill(
            name="After pay mortgage",
            amount=1942,
            due_date=today + timedelta(days=10),
            required=True,
        )
    )
    session.commit()
    cf = calculate_cashflow(session, starting_cash=1340)
    assert cf["cycle_obligations"] >= 2142
    assert cf["reserved_for_pay_cycle"] >= 200
    assert cf["flexible_before_next_income"] < 1340 - 200
