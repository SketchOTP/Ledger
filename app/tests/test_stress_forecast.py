"""Stress forecast acceptance tests — DB must never mutate."""

from datetime import date, timedelta

from app.core.db_fingerprint import capture_db_fingerprint
from app.core.financial_snapshot import build_financial_snapshot
from app.core.stress_forecast import build_stress_forecast, run_stress_forecast
from app.db.models import Bill, BorrowerProfile, Debt, IncomeSource


def test_forecast_does_not_mutate_database(session, sample_data):
    before = capture_db_fingerprint(session)
    run_stress_forecast(session, horizon_days=30)
    run_stress_forecast(session, horizon_days=90)
    after = capture_db_fingerprint(session)
    assert before == after


def test_detects_shortfall_date(session):
    today = date.today()
    session.add(
        IncomeSource(
            source_name="Job",
            amount=500,
            next_pay_date=today + timedelta(days=28),
        )
    )
    session.add(
        Bill(
            name="Rent",
            amount=2000,
            due_date=today + timedelta(days=3),
            required=True,
        )
    )
    session.commit()
    result = run_stress_forecast(session, horizon_days=30, starting_cash_override=100)
    assert result["first_shortfall_date"] is not None
    assert result["pressure_score"] >= 40


def test_forecast_ignores_env_buffer_when_profile_target_zero(session):
    today = date.today()
    session.add(BorrowerProfile(target_buffer=0, emergency_fund_balance=100))
    session.add(
        IncomeSource(
            source_name="Job",
            amount=3000,
            next_pay_date=today + timedelta(days=14),
        )
    )
    session.add(
        Bill(name="Utilities", amount=400, due_date=today + timedelta(days=5), required=True)
    )
    session.commit()
    result = run_stress_forecast(session, horizon_days=60, starting_cash_override=800)
    assert result["first_buffer_breach_date"] is None
    assert not any(
        "buffer" in (r or "").lower()
        for r in result.get("recommendations") or []
        if "breach" in (r or "").lower()
    )


def test_detects_buffer_breach(session):
    today = date.today()
    session.add(
        BorrowerProfile(target_buffer=2000, emergency_fund_balance=1500)
    )
    session.add(
        IncomeSource(
            source_name="Job",
            amount=3000,
            next_pay_date=today + timedelta(days=14),
        )
    )
    session.add(
        Bill(name="Utilities", amount=400, due_date=today + timedelta(days=5), required=True)
    )
    session.commit()
    result = run_stress_forecast(session, horizon_days=60, starting_cash_override=800)
    assert result["first_buffer_breach_date"] is not None


def test_scores_danger_above_watch(session):
    today = date.today()
    session.add(
        IncomeSource(
            source_name="Thin",
            amount=200,
            next_pay_date=today + timedelta(days=30),
        )
    )
    for i in range(4):
        session.add(
            Bill(
                name=f"Bill{i}",
                amount=350,
                due_date=today + timedelta(days=2 + i),
                required=True,
            )
        )
    session.add(Debt(creditor="Card", balance=5000, apr=24, minimum_payment=200))
    session.commit()
    result = run_stress_forecast(session, horizon_days=90, starting_cash_override=50)
    assert result["pressure_score"] >= 50
    assert result["status"] in ("exposed", "danger")
    watch_score = 49
    assert result["pressure_score"] > watch_score or result["status"] in ("exposed", "danger")


def test_bill_cluster_creates_risk_window(session):
    today = date.today()
    pay = today + timedelta(days=10)
    session.add(
        IncomeSource(source_name="Job", amount=2500, next_pay_date=pay)
    )
    for i in range(3):
        session.add(
            Bill(
                name=f"Cluster{i}",
                amount=200,
                due_date=pay - timedelta(days=4 - i),
                required=True,
            )
        )
    session.commit()
    result = run_stress_forecast(session, horizon_days=30, starting_cash_override=500)
    types = {w["type"] for w in result["risk_windows"]}
    assert "bill_cluster_before_paycheck" in types


def test_paycheck_reduces_pressure(session):
    today = date.today()
    session.add(
        Bill(name="Rent", amount=900, due_date=today + timedelta(days=7), required=True)
    )
    session.commit()
    low_income = run_stress_forecast(
        session,
        horizon_days=30,
        starting_cash_override=200,
    )
    session.add(
        IncomeSource(
            source_name="Job",
            amount=4000,
            next_pay_date=today + timedelta(days=7),
        )
    )
    session.commit()
    with_pay = run_stress_forecast(
        session,
        horizon_days=30,
        starting_cash_override=200,
    )
    assert with_pay["pressure_score"] <= low_income["pressure_score"]


def test_timeline_length_matches_horizon(session, sample_data):
    for days in (30, 60, 90, 180):
        result = run_stress_forecast(session, horizon_days=days)
        assert len(result["timeline"]) == days
        assert result["horizon_days"] == days


def test_build_from_snapshot_without_session(session, sample_data):
    snap = build_financial_snapshot(session)
    out = build_stress_forecast(snap, horizon_days=30)
    assert out["snapshot_id"] == snap.snapshot_id
    assert len(out["timeline"]) == 30
