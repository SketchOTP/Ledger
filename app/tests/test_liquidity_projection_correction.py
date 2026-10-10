"""Merge-gate regression tests for liquidity projection correction branch."""

from datetime import date, timedelta

from app.core.cashflow_engine import calculate_cashflow
from app.core.liquidity_planner import (
    LIQUIDITY_UNDATED_WARNING,
    _collect_events,
    _daily_balances,
    _simulate_waterfall,
    build_liquidity_plan,
)
from app.db.models import (
    Bill,
    BorrowerProfile,
    CreditCard,
    Debt,
    IncomeSource,
    MiscSpending,
    MortgageAccount,
)

CASH_STATUS_KEYS = {"checking_balance", "starting_cash", "projected_cash_after_30_days"}


def test_past_due_obligations_simulated_on_today(session):
    """Unpaid obligations scheduled before today must hit today's simulation date."""
    today = date.today()
    session.add(
        Debt(
            creditor="53rd Bank",
            balance=5000,
            apr=17,
            minimum_payment=255,
            due_date=today - timedelta(days=2),
            recurrence="monthly",
        )
    )
    session.commit()

    events, _ = _collect_events(session, today + timedelta(days=180))
    past_due_rows = [
        e
        for e in events
        if e.kind == "obligation"
        and e.due_date
        and e.due_date < today
        and e.when <= today + timedelta(days=30)
    ]
    assert past_due_rows
    assert all(e.when == today for e in past_due_rows)


def test_past_due_obligations_not_double_counted(session):
    """Each obligation occurrence (ref + original due date) appears at most once."""
    today = date.today()
    session.add(
        Bill(
            name="Water",
            amount=75,
            due_date=today - timedelta(days=5),
            recurrence="monthly",
            required=True,
        )
    )
    session.add(
        Debt(
            creditor="53rd Bank",
            balance=5000,
            apr=17,
            minimum_payment=255,
            due_date=today - timedelta(days=2),
            recurrence="monthly",
        )
    )
    session.commit()

    events, _ = _collect_events(session, today + timedelta(days=180))
    keys = [
        (e.ref_id, e.due_date.isoformat() if e.due_date else "")
        for e in events
        if e.kind == "obligation"
    ]
    assert len(keys) == len(set(keys))


def test_projected_balance_carries_unpaid_shortfall_instead_of_forgetting_it(session):
    """A blocked payment is still owed in the 30-day signed projection."""
    today = date.today()
    session.add(
        Bill(name="Huge", amount=5000, due_date=today + timedelta(days=1), required=True)
    )
    session.add(
        IncomeSource(
            source_name="Job",
            amount=2000,
            pay_frequency="biweekly",
            next_pay_date=today + timedelta(days=15),
        )
    )
    session.commit()

    plan = build_liquidity_plan(session, starting_cash=100)
    events, _ = _collect_events(session, today + timedelta(days=180))
    ending, _, _ = _simulate_waterfall(100, events, through=today + timedelta(days=30))
    daily_end = _daily_balances(100, events, 180)[0][30]["balance"]

    assert daily_end < 0
    assert plan["horizons"][30]["projected_balance"] == daily_end
    assert plan["thirty_day_outlook"]["projected_balance"] == daily_end
    assert plan["horizons"][30]["waterfall_funded_balance"] == round(ending, 2)
    assert ending > daily_end


def test_zero_projected_balance_preserved(session):
    """Valid $0 projected balance must flow through cashflow and command center."""
    session.add(BorrowerProfile(checking_balance=0, target_buffer=0))
    session.commit()

    cf = calculate_cashflow(session)
    assert cf["thirty_day_outlook"]["projected_balance"] == 0.0
    assert cf.get("projected_cash_after_30_days") == 0.0


def test_partner_missing_next_pay_date_warning_and_excluded_income(session):
    """Weekly income without next_pay_date is diagnostic-only until fixed."""
    today = date.today()
    session.add(BorrowerProfile(checking_balance=823, target_buffer=0))
    session.add(
        IncomeSource(
            source_name="Primary Job",
            amount=2230,
            pay_frequency="biweekly",
            next_pay_date=today + timedelta(days=3),
        )
    )
    session.add(
        IncomeSource(source_name="Partner", amount=300, pay_frequency="weekly"),
    )
    session.commit()

    plan = build_liquidity_plan(session)
    diag = plan["liquidity_diagnostics"]
    assert diag["warning_count"] == 1
    partner = diag["skipped_undated_events"][0]
    assert partner["label"] == "Partner"
    assert partner["missing_field"] == "next_pay_date"

    h30 = plan["horizons"][30]
    assert h30["income_expected"] == 4460.0
    assert h30["income_expected"] < 4460 + 300
    assert LIQUIDITY_UNDATED_WARNING in (calculate_cashflow(session).get("warnings") or [])


def test_liquidity_diagnostics_additive_api_contract(session):
    """Diagnostics are additive; prior command-center cash fields remain present."""
    session.add(BorrowerProfile(checking_balance=500, target_buffer=0))
    session.commit()

    cash = calculate_cashflow(session)
    assert CASH_STATUS_KEYS.issubset(cash.keys())
    assert "liquidity_diagnostics" in cash
    assert cash["liquidity_diagnostics"]["warning_count"] == 0


def test_safe_to_spend_zero_when_waterfall_shortfall(session):
    """Timeline safe-to-spend stays $0 when the 30-day waterfall has a shortfall."""
    today = date.today()
    session.add(
        Bill(name="Huge", amount=5000, due_date=today + timedelta(days=1), required=True)
    )
    session.commit()

    plan = build_liquidity_plan(session, starting_cash=100)
    assert any(s["action"] == "shortfall" for s in plan["playbook"])
    assert plan["safe_to_spend"] == 0.0
    assert plan["thirty_day_outlook"]["safe_to_spend"] == 0.0
    assert plan["flexible_today"] == 0.0
    assert plan["flexible_today_trace"]["has_shortfall"] is True


def test_realistic_month_scenario_projected_balance(session):
    """Household-like month: multi-income, full obligation mix, deterministic day-30 balance."""
    today = date.today()
    session.add(BorrowerProfile(checking_balance=823, target_buffer=500))
    session.add(
        IncomeSource(
            source_name="Primary Job",
            amount=2230,
            pay_frequency="biweekly",
            next_pay_date=today + timedelta(days=3),
        )
    )
    session.add(
        IncomeSource(
            source_name="Partner",
            amount=300,
            pay_frequency="weekly",
            next_pay_date=today + timedelta(days=2),
        )
    )
    session.add(
        Bill(
            name="Car Insurance",
            amount=185,
            due_date=today + timedelta(days=2),
            recurrence="monthly",
            required=True,
        )
    )
    session.add(
        Debt(
            creditor="53rd Bank",
            balance=5000,
            apr=17,
            minimum_payment=255,
            due_date=today + timedelta(days=12),
            recurrence="monthly",
        )
    )
    session.add(
        MortgageAccount(
            lender="Citizens Bank",
            original_balance=200000,
            current_balance=200000,
            interest_rate=5,
            monthly_payment=1942,
            due_by_date=today + timedelta(days=8),
            recurrence="monthly",
        )
    )
    session.add(
        CreditCard(
            issuer="Chase",
            card_name="Freedom",
            balance=500,
            credit_limit=5000,
            apr=24.9,
            minimum_payment=25,
            due_date=today + timedelta(days=6),
            recurrence="monthly",
        )
    )
    session.add(
        MiscSpending(
            name="Fuel",
            category="other",
            amount=30,
            due_date=today + timedelta(days=5),
            recurrence="monthly",
        )
    )
    session.add(
        MiscSpending(
            name="Claude",
            category="misc",
            amount=20,
            due_date=today + timedelta(days=7),
            recurrence="monthly",
        )
    )
    session.commit()

    plan = build_liquidity_plan(session)
    h30 = plan["horizons"][30]
    events, _ = _collect_events(session, today + timedelta(days=180))
    ending, steps, _ = _simulate_waterfall(823, events, through=today + timedelta(days=30))
    last_checking = next(
        s["checking_balance"] for s in reversed(steps) if s.get("checking_balance") is not None
    )

    assert h30["income_expected"] == 5960.0
    assert h30["obligations_scheduled"] == 2457.0
    assert h30["net_scheduled"] == 3503.0
    assert plan["thirty_day_outlook"]["projected_balance"] == 4326.0
    assert h30["projected_balance"] == 4326.0
    assert ending == 4326.0
    assert last_checking == 4326.0
    assert plan["thirty_day_outlook"]["projected_balance"] == round(823 + h30["net_scheduled"], 2)

    pays = {s["name"] for s in plan["playbook"] if s["action"] == "pay_from_checking"}
    assert "Car Insurance" in pays
    assert any("Citizens Bank" in name for name in pays)
    assert any("53rd Bank" in name for name in pays)
    assert any("Chase" in name for name in pays)
    assert "Fuel" in pays
    assert "Claude" in pays

    deposits = [s for s in plan["playbook"] if s["action"] == "deposit_to_checking"]
    assert any(s["name"] == "Primary Job" for s in deposits)
    assert any(s["name"] == "Partner" for s in deposits)
