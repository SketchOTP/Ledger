"""Financial timeline and memory layer tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlmodel import Session, select

from app.core.financial_snapshot import build_financial_snapshot
from app.core.financial_timeline import (
    detect_events_from_snapshots,
    persist_financial_snapshot,
    seed_snapshots_for_tests,
)
from app.db.models import FinancialAction, FinancialTimelineEvent


def _metrics(
    *,
    total_debt: float = 5000,
    monthly_surplus: float = 500,
    emergency_buffer: float = 1000,
    grocery_pressure: float = 400,
    pressure_score: float = 30,
) -> dict:
    return {
        "total_debt": total_debt,
        "monthly_surplus": monthly_surplus,
        "emergency_buffer": emergency_buffer,
        "grocery_pressure": grocery_pressure,
        "pressure_score": pressure_score,
        "credit_utilization": 40,
    }


def test_snapshot_persistence_works(session: Session, sample_data):
    snap = build_financial_snapshot(session)
    row = persist_financial_snapshot(session, cadence="daily", snapshot=snap, force=True)
    assert row is not None
    assert row.id
    assert row.pressure_score >= 0
    assert row.cadence == "daily"


def test_debt_reduction_event_detected(session: Session):
    today = date.today()
    seed_snapshots_for_tests(
        session,
        rows=[
            {
                "cadence": "weekly",
                "snapshot_date": (today - timedelta(days=14)).isoformat(),
                "grocery_pressure": 400,
                "pressure_score": 40,
                "metrics": _metrics(total_debt=8000),
            },
            {
                "cadence": "weekly",
                "snapshot_date": today.isoformat(),
                "grocery_pressure": 400,
                "pressure_score": 35,
                "metrics": _metrics(total_debt=6500),
            },
        ],
    )
    events = detect_events_from_snapshots(session, emit_events=False)
    types = {e.event_type for e in events}
    assert "debt_reduced" in types


def test_improving_pressure_score_detected(session: Session):
    today = date.today()
    seed_snapshots_for_tests(
        session,
        rows=[
            {
                "cadence": "daily",
                "snapshot_date": (today - timedelta(days=7)).isoformat(),
                "pressure_score": 65,
                "metrics": _metrics(pressure_score=65),
            },
            {
                "cadence": "daily",
                "snapshot_date": today.isoformat(),
                "pressure_score": 50,
                "metrics": _metrics(pressure_score=50),
            },
        ],
    )
    events = detect_events_from_snapshots(session, emit_events=False)
    assert any(e.event_type == "forecast_risk_reduction" for e in events)


def test_savings_win_on_timeline(session: Session):
    act = FinancialAction(
        title="Cancel gym",
        status="completed",
        actual_monthly_savings=40.0,
        completed_at=datetime.now(UTC).replace(tzinfo=None),
    )
    session.add(act)
    session.commit()
    from app.core.financial_timeline import sync_action_timeline_events

    sync_action_timeline_events(session, emit_events=False)
    events = session.exec(
        select(FinancialTimelineEvent).where(FinancialTimelineEvent.event_type == "savings_win")
    ).all()
    assert len(events) >= 1
