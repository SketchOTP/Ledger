"""Liquidity planner / timeline waterfall tests."""

from datetime import date, timedelta

from sqlmodel import select

from app.core.cashflow_engine import calculate_cashflow
from app.core.income_payment import mark_income_received
from app.core.liquidity_planner import _collect_events, build_liquidity_plan
from app.db.models import Bill, BorrowerProfile, Debt, IncomeSource, MortgageAccount


def test_missing_next_pay_date_does_not_crash(session, sample_data):
    """sample_data income has no next_pay_date — planner must still build."""
    plan = build_liquidity_plan(session, starting_cash=5000)
    assert plan["starting_cash"] == 5000
    assert plan["safe_to_spend"] >= 0
    assert isinstance(plan["playbook"], list)


def test_missing_bill_due_date_does_not_crash(session):
    """Bills without due_date use schedule anchors; sort must not TypeError."""
    today = date.today()
    session.add(
        Bill(name="Rent", amount=1200, required=True),
    )
    session.add(
        IncomeSource(
            source_name="Job",
            amount=2000,
            pay_frequency="biweekly",
            next_pay_date=today + timedelta(days=7),
        )
    )
    session.commit()
    plan = build_liquidity_plan(session, starting_cash=3000)
    assert plan["horizons"][30]["obligations_scheduled"] >= 1200
    step_dates = [s["date"] for s in plan["playbook"]]
    assert step_dates == sorted(step_dates)


def test_mixed_valid_and_past_due_dates_sort(session):
    """Past-due (when=today, due_date=historical) must sort with scheduled events."""
    today = date.today()
    session.add(
        Bill(
            name="Insurance",
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
            due_date=today - timedelta(days=1),
            recurrence="monthly",
        )
    )
    session.add(
        IncomeSource(
            source_name="Job",
            amount=2230,
            pay_frequency="biweekly",
            next_pay_date=today + timedelta(days=8),
        )
    )
    session.commit()
    through = today + timedelta(days=180)
    events, _ = _collect_events(session, through)
    assert events == sorted(events, key=lambda e: e.sort_key)
    assert not any(e.when is None for e in events)


def test_valid_date_output_unchanged(session):
    """Fully dated scenario: playbook order and totals stay stable."""
    today = date.today()
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
        MortgageAccount(
            lender="Citizens Bank",
            original_balance=200000,
            current_balance=200000,
            interest_rate=5,
            monthly_payment=1942,
            due_by_date=today + timedelta(days=10),
            recurrence="monthly",
        )
    )
    session.commit()
    plan = build_liquidity_plan(session, starting_cash=2000)
    steps = plan["playbooks"][30]
    car_first = min(
        date.fromisoformat(s["date"][:10]) for s in steps if s.get("name") == "Car Insurance"
    )
    mortgage_first = min(
        date.fromisoformat(s["date"][:10])
        for s in steps
        if "Citizens Bank" in (s.get("name") or "")
    )
    assert car_first < mortgage_first
    assert plan["horizons"][30]["obligations_scheduled"] >= 185 + 1942


def test_cashflow_sample_data_no_typeerror(session, sample_data):
    cf = calculate_cashflow(session, starting_cash=5000)
    assert cf["risk_level"] in ("safe", "tight", "danger")
    assert cf["safe_to_spend"] >= 0


def test_user_scenario_waterfall(session):
    """Checking covers past-due + insurance; after paycheck marked received, mortgage clears."""
    today = date.today()
    pay_date = today + timedelta(days=8)
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
        IncomeSource(
            source_name="Primary Job",
            amount=2230,
            pay_frequency="biweekly",
            next_pay_date=pay_date,
        )
    )
    session.add(
        MortgageAccount(
            lender="Citizens Bank",
            original_balance=200000,
            current_balance=200000,
            interest_rate=5,
            monthly_payment=1942,
            due_by_date=today + timedelta(days=10),
            recurrence="monthly",
        )
    )
    session.commit()

    plan_before = build_liquidity_plan(session, starting_cash=1340)
    assert any(s["action"] == "deposit_to_checking" for s in plan_before["playbook"])
    assert plan_before["horizons"][30]["income_expected"] >= 2230
    assert not any(s["action"] == "shortfall" for s in plan_before["playbook"])

    src = session.exec(select(IncomeSource)).first()
    assert src is not None
    mark_income_received(session, src, pay_date)

    # User updates checking manually when the deposit lands.
    plan = build_liquidity_plan(session, starting_cash=1340 + 2230)
    assert plan["balance_after_pay_cycle"] > 0
    assert plan["safe_to_spend"] >= 0
    assert not any(s["action"] == "shortfall" for s in plan["playbook"])
    assert not any(
        s["action"] == "deposit_to_checking"
        and str(s.get("date", ""))[:10] == pay_date.isoformat()
        for s in plan["playbook"]
    )
    assert plan["horizons"][30]["obligations_scheduled"] >= 255 + 185 + 1942
    assert len(plan["recommendations"]) >= 2


def test_playbook_timeline_sorted_by_date(session):
    """Timeline UI order: soonest obligations before later mortgage."""
    today = date.today()
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
        MortgageAccount(
            lender="Citizens Bank",
            original_balance=200000,
            current_balance=200000,
            interest_rate=5,
            monthly_payment=1942,
            due_by_date=today + timedelta(days=10),
            recurrence="monthly",
        )
    )
    session.commit()
    plan = build_liquidity_plan(session, starting_cash=2000)
    steps = plan["playbooks"][30]
    assert plan.get("playbook_timeline_days") == 30
    assert set(plan["playbooks"].keys()) == {30, 60, 90, 180, "6mo"}
    assert len(plan["playbooks"][180]) >= len(steps)
    step_dates = [date.fromisoformat(s["date"][:10]) for s in steps]
    assert step_dates == sorted(step_dates)
    car_first = min(
        date.fromisoformat(s["date"][:10]) for s in steps if s.get("name") == "Car Insurance"
    )
    mortgage_first = min(
        date.fromisoformat(s["date"][:10])
        for s in steps
        if "Citizens Bank" in (s.get("name") or "")
    )
    assert car_first < mortgage_first
    last = max(step_dates)
    assert (last - today).days <= 30
    last_180 = max(date.fromisoformat(s["date"][:10]) for s in plan["playbooks"][180])
    assert (last_180 - today).days <= 180


def test_forward_plan_includes_scheduled_paychecks(session):
    """Safe to spend uses 30-day waterfall with planned income, not checking-only."""
    today = date.today()
    session.add(
        IncomeSource(
            source_name="Job",
            amount=2000,
            pay_frequency="biweekly",
            next_pay_date=today,
        )
    )
    session.add(
        Bill(
            name="Rent",
            amount=1500,
            due_date=today + timedelta(days=5),
            recurrence="monthly",
            required=True,
        )
    )
    session.commit()
    plan = build_liquidity_plan(session, starting_cash=400)
    outlook = plan["thirty_day_outlook"]
    assert outlook["status"] == "ok"
    assert outlook["scheduled_income"] >= 2000
    assert outlook["scheduled_obligations"] >= 1500
    assert plan["safe_to_spend"] > 0
    assert not any(s["action"] == "shortfall" for s in plan["playbook"])


def test_shortfall_when_cannot_cover(session):
    today = date.today()
    session.add(
        Bill(name="Huge", amount=5000, due_date=today + timedelta(days=1), required=True)
    )
    session.commit()
    plan = build_liquidity_plan(session, starting_cash=100)
    assert any(s["action"] == "shortfall" for s in plan["playbook"])


def test_projected_balance_matches_waterfall_ending(session):
    """Projected after 30 days must equal the playbook waterfall ending balance."""
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
    session.commit()

    plan = build_liquidity_plan(session)
    h30 = plan["horizons"][30]
    outlook = plan["thirty_day_outlook"]
    projected = outlook["projected_balance"]
    assert projected == h30["projected_balance"]
    assert projected == round(823 + h30["net_scheduled"], 2)
    assert h30["obligations_scheduled"] >= 185 + 1942
    assert h30["income_expected"] >= 2230 + 300
    assert projected < 823 + h30["income_expected"]


def test_past_due_mortgage_counts_in_projection(session):
    """Unpaid mortgage due before today must reduce the 30-day projection."""
    today = date.today()
    session.add(BorrowerProfile(checking_balance=2000, target_buffer=0))
    session.add(
        IncomeSource(
            source_name="Job",
            amount=2000,
            pay_frequency="monthly",
            next_pay_date=today + timedelta(days=14),
        )
    )
    session.add(
        MortgageAccount(
            lender="Citizens Bank",
            original_balance=200000,
            current_balance=200000,
            interest_rate=5,
            monthly_payment=1942,
            due_by_date=today - timedelta(days=4),
            recurrence="monthly",
        )
    )
    session.commit()

    plan = build_liquidity_plan(session)
    assert plan["horizons"][30]["obligations_scheduled"] >= 1942
    assert plan["thirty_day_outlook"]["projected_balance"] <= 2000 + 2000 - 1942 + 1


def test_liquidity_diagnostics_missing_income_next_pay_date(session):
    today = date.today()
    session.add(
        IncomeSource(source_name="Partner", amount=300, pay_frequency="weekly"),
    )
    session.add(
        Bill(
            name="Rent",
            amount=1200,
            due_date=today + timedelta(days=10),
            required=True,
            recurrence="monthly",
        )
    )
    session.commit()

    plan = build_liquidity_plan(session, starting_cash=1000)
    diag = plan["liquidity_diagnostics"]
    assert diag["warning_count"] == 1
    assert diag["skipped_undated_events"][0]["type"] == "income"
    assert diag["skipped_undated_events"][0]["missing_field"] == "next_pay_date"


def test_liquidity_diagnostics_none_when_fully_dated(session):
    today = date.today()
    session.add(
        IncomeSource(
            source_name="Job",
            amount=2000,
            pay_frequency="biweekly",
            next_pay_date=today,
        )
    )
    session.add(
        Bill(name="Rent", amount=1200, due_date=today + timedelta(days=5), required=True),
    )
    session.commit()

    plan = build_liquidity_plan(session, starting_cash=500)
    assert plan["liquidity_diagnostics"]["warning_count"] == 0


def test_cashflow_and_command_center_expose_diagnostics(session):
    session.add(BorrowerProfile(checking_balance=500, target_buffer=0))
    session.add(
        IncomeSource(source_name="Side gig", amount=300, pay_frequency="weekly"),
    )
    session.commit()

    cf = calculate_cashflow(session)
    assert cf["liquidity_diagnostics"]["warning_count"] == 1
    assert any("missing dates" in w.lower() for w in cf.get("warnings", []))

    assert cf["liquidity_diagnostics"]["warning_count"] == 1
    assert cf["projected_cash_after_30_days"] == cf["thirty_day_outlook"]["projected_balance"]


def test_six_month_horizon_in_liquidity_plan(session):
    from datetime import date

    from app.core.liquidity_planner import HORIZON_6MO, _horizon_span_days

    session.add(BorrowerProfile(checking_balance=2000, target_buffer=500))
    session.commit()
    plan = build_liquidity_plan(session)
    assert HORIZON_6MO in plan["horizons"]
    assert HORIZON_6MO in plan["playbooks"]
    six = plan["horizons"][HORIZON_6MO]
    assert six["horizon_key"] == HORIZON_6MO
    assert six["days"] == _horizon_span_days(date.today(), HORIZON_6MO)
    assert six["days"] > 180
    assert "flexible_today" in six
    assert isinstance(six["projected_balance"], float)
