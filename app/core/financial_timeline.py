"""Financial timeline — periodic snapshots and derived change events."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlmodel import Session, select

from app.core.data_quality import assess_data_quality
from app.core.financial_snapshot import FinancialStateSnapshot, build_financial_snapshot
from app.core.stress_forecast import build_stress_forecast
from app.db.models import (
    FinancialAction,
    FinancialSnapshotRecord,
    FinancialTimelineEvent,
    Recommendation,
)
from app.events.bus import event_bus
from app.events.types import Event, EventType

CADENCES = ("daily", "weekly", "monthly")
CADENCE_MIN_DAYS = {"daily": 1, "weekly": 7, "monthly": 28}
MIN_SNAPSHOTS_FOR_TREND = 3
DEBT_REDUCTION_MIN_PCT = 0.03
DEBT_REDUCTION_MIN_ABS = 100.0
CASHFLOW_DETERIORATION_MIN = 150.0
GROCERY_SPIKE_MIN_PCT = 0.12
PRESSURE_IMPROVE_MIN = 5.0
BUFFER_GROW_MIN = 200.0

TIMELINE_EVENT_TYPES = frozenset({
    "debt_reduced",
    "mortgage_rate_improved",
    "cashflow_deteriorated",
    "emergency_buffer_grew",
    "grocery_spending_spike",
    "insurance_cost_increase",
    "subscription_removed",
    "high_apr_eliminated",
    "forecast_risk_increase",
    "forecast_risk_reduction",
    "action_completed",
    "savings_win",
})


def _today() -> date:
    return date.today()


def _total_debt_from_snapshot(snapshot: FinancialStateSnapshot) -> float:
    block = (snapshot.debts or {}).get("avalanche") or {}
    if isinstance(block, dict):
        ranked = block.get("order") or []
    elif isinstance(block, list):
        ranked = block
    else:
        ranked = []
    return round(sum(float(d.get("balance") or 0) for d in ranked if isinstance(d, dict)), 2)


def _avg_credit_utilization(snapshot: FinancialStateSnapshot) -> float:
    cards = snapshot.credit or []
    if not cards:
        return 0.0
    utils = [float(c.get("utilization_pct") or 0) for c in cards if c.get("utilization_pct") is not None]
    if not utils:
        limits = [float(c.get("credit_limit") or 0) for c in cards]
        balances = [float(c.get("balance") or 0) for c in cards]
        pairs = [(b, lim) for b, lim in zip(balances, limits, strict=False) if lim > 0]
        if not pairs:
            return 0.0
        utils = [b / lim * 100 for b, lim in pairs]
    return round(sum(utils) / len(utils), 2) if utils else 0.0


def _grocery_pressure(snapshot: FinancialStateSnapshot) -> float:
    g = snapshot.groceries or {}
    stores = g.get("shopping_list_total_by_store") or []
    if stores:
        return round(float(stores[0].get("total", 0)) * 4.33, 2)
    weekly = g.get("weekly_estimate")
    if weekly is not None:
        return round(float(weekly) * 4.33, 2)
    return 0.0


def _emergency_buffer(snapshot: FinancialStateSnapshot) -> float:
    if snapshot.borrower:
        return float(snapshot.borrower.get("emergency_fund_balance") or 0)
    return 0.0


def _monthly_surplus(snapshot: FinancialStateSnapshot) -> float:
    cf = snapshot.cashflow or {}
    return float(cf.get("monthly_surplus") or cf.get("safe_to_spend") or 0)


def _metrics_from_live(snapshot: FinancialStateSnapshot, session: Session) -> dict[str, Any]:
    forecast = build_stress_forecast(snapshot, horizon_days=90)
    quality = assess_data_quality(session, emit_events=False)
    rec_count = len(
        list(session.exec(select(Recommendation).where(Recommendation.status == "active")).all())
    )
    return {
        "pressure_score": float(forecast.get("pressure_score") or 0),
        "forecast_status": forecast.get("status"),
        "data_quality_score": float(quality.get("overall_score") or 0),
        "recommendation_count": rec_count,
        "total_debt": _total_debt_from_snapshot(snapshot),
        "credit_utilization": _avg_credit_utilization(snapshot),
        "grocery_pressure": _grocery_pressure(snapshot),
        "emergency_buffer": _emergency_buffer(snapshot),
        "monthly_surplus": _monthly_surplus(snapshot),
        "health_score": float((snapshot.health or {}).get("score") or 0),
    }


def snapshot_record_from_live(
    session: Session,
    snapshot: FinancialStateSnapshot,
    *,
    cadence: str,
    snapshot_date: date | None = None,
) -> FinancialSnapshotRecord:
    """Build a DB row from a live FinancialStateSnapshot (no persist)."""
    if cadence not in CADENCES:
        raise ValueError(f"cadence must be one of {CADENCES}")
    metrics = _metrics_from_live(snapshot, session)
    return FinancialSnapshotRecord(
        cadence=cadence,
        snapshot_date=snapshot_date or _today(),
        cashflow_json=json.dumps(snapshot.cashflow or {}),
        debts_json=json.dumps(snapshot.debts or {}),
        dti_json=json.dumps(snapshot.dti or {}),
        credit_util_json=json.dumps({"avg_utilization_pct": metrics["credit_utilization"]}),
        mortgage_json=json.dumps(snapshot.mortgage or []),
        grocery_pressure=metrics["grocery_pressure"],
        recommendation_count=metrics["recommendation_count"],
        pressure_score=metrics["pressure_score"],
        data_quality_score=metrics["data_quality_score"],
        metrics_json=json.dumps(metrics),
        source_snapshot_id=snapshot.snapshot_id,
    )


def _should_capture(session: Session, cadence: str, as_of: date) -> bool:
    min_days = CADENCE_MIN_DAYS[cadence]
    latest = session.exec(
        select(FinancialSnapshotRecord)
        .where(FinancialSnapshotRecord.cadence == cadence)
        .order_by(FinancialSnapshotRecord.snapshot_date.desc())  # type: ignore[arg-type]
    ).first()
    if latest is None:
        return True
    return (as_of - latest.snapshot_date).days >= min_days


def persist_financial_snapshot(
    session: Session,
    *,
    cadence: str = "daily",
    snapshot: FinancialStateSnapshot | None = None,
    snapshot_date: date | None = None,
    force: bool = False,
    emit_events: bool = True,
) -> FinancialSnapshotRecord | None:
    """Persist one periodic snapshot if cadence window allows (or force=True)."""
    as_of = snapshot_date or _today()
    if not force and not _should_capture(session, cadence, as_of):
        return None

    live = snapshot or build_financial_snapshot(session)
    row = snapshot_record_from_live(session, live, cadence=cadence, snapshot_date=as_of)
    session.add(row)
    session.commit()
    session.refresh(row)

    if emit_events:
        event_bus.emit(
            Event(
                event_type=EventType.FINANCIAL_SNAPSHOT_CREATED.value,
                source="engine",
                payload={
                    "snapshot_id": row.id,
                    "cadence": cadence,
                    "snapshot_date": as_of.isoformat(),
                    "pressure_score": row.pressure_score,
                },
            ),
            session=session,
        )

    detect_events_from_snapshots(session, emit_events=emit_events)
    return row


def maybe_capture_all_cadences(
    session: Session,
    *,
    snapshot: FinancialStateSnapshot | None = None,
    emit_events: bool = True,
) -> list[FinancialSnapshotRecord]:
    """Try daily, weekly, and monthly captures in one pass."""
    stored: list[FinancialSnapshotRecord] = []
    for cadence in CADENCES:
        row = persist_financial_snapshot(
            session,
            cadence=cadence,
            snapshot=snapshot,
            emit_events=emit_events,
        )
        if row is not None:
            stored.append(row)
    return stored


def list_snapshots(
    session: Session,
    *,
    cadence: str | None = None,
    limit: int = 120,
) -> list[FinancialSnapshotRecord]:
    stmt = select(FinancialSnapshotRecord).order_by(
        FinancialSnapshotRecord.snapshot_date.desc(),  # type: ignore[arg-type]
        FinancialSnapshotRecord.created_at.desc(),  # type: ignore[arg-type]
    )
    if cadence:
        stmt = stmt.where(FinancialSnapshotRecord.cadence == cadence)
    return list(session.exec(stmt).all())[:limit]


def _load_metrics(row: FinancialSnapshotRecord) -> dict[str, Any]:
    try:
        return json.loads(row.metrics_json or "{}")
    except json.JSONDecodeError:
        return {}


def _provenance(
    *,
    rule: str,
    before_id: str | None = None,
    after_id: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "rule": rule,
        "before_snapshot_id": before_id,
        "after_snapshot_id": after_id,
        "evidence": evidence or {},
    }


def _store_timeline_event(
    session: Session,
    *,
    event_type: str,
    title: str,
    description: str,
    occurred_at: date,
    severity: str = "info",
    provenance: dict[str, Any],
    payload: dict[str, Any] | None = None,
    emit_events: bool = True,
) -> FinancialTimelineEvent:
    existing = session.exec(
        select(FinancialTimelineEvent).where(
            FinancialTimelineEvent.event_type == event_type,
            FinancialTimelineEvent.occurred_at == occurred_at,
            FinancialTimelineEvent.title == title,
        )
    ).first()
    if existing:
        return existing

    row = FinancialTimelineEvent(
        event_type=event_type,
        title=title,
        description=description,
        severity=severity,
        occurred_at=occurred_at,
        provenance_json=json.dumps(provenance),
        payload_json=json.dumps(payload or {}),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def detect_events_from_snapshots(session: Session, *, emit_events: bool = True) -> list[FinancialTimelineEvent]:
    """Compare recent snapshots and emit timeline events."""
    snapshots = list_snapshots(session, limit=60)
    if len(snapshots) < 2:
        return []

    events: list[FinancialTimelineEvent] = []
    by_cadence: dict[str, list[FinancialSnapshotRecord]] = {}
    for s in snapshots:
        by_cadence.setdefault(s.cadence, []).append(s)

    for cadence, rows in by_cadence.items():
        rows = sorted(rows, key=lambda r: r.snapshot_date)
        if len(rows) < 2:
            continue
        prev, curr = rows[-2], rows[-1]
        prev_m = _load_metrics(prev)
        curr_m = _load_metrics(curr)
        occurred = curr.snapshot_date

        prev_debt = float(prev_m.get("total_debt") or 0)
        curr_debt = float(curr_m.get("total_debt") or 0)
        if prev_debt > 0 and curr_debt < prev_debt:
            drop = prev_debt - curr_debt
            if drop >= DEBT_REDUCTION_MIN_ABS or drop / prev_debt >= DEBT_REDUCTION_MIN_PCT:
                ev = _store_timeline_event(
                    session,
                    event_type="debt_reduced",
                    title="Debt balance decreased",
                    description=f"Total debt fell by ${drop:,.0f} ({cadence} snapshot).",
                    occurred_at=occurred,
                    severity="info",
                    provenance=_provenance(
                        rule="debt_reduced",
                        before_id=prev.id,
                        after_id=curr.id,
                        evidence={"prev_debt": prev_debt, "curr_debt": curr_debt},
                    ),
                    emit_events=emit_events,
                )
                events.append(ev)

        prev_surplus = float(prev_m.get("monthly_surplus") or 0)
        curr_surplus = float(curr_m.get("monthly_surplus") or 0)
        if curr_surplus < prev_surplus - CASHFLOW_DETERIORATION_MIN:
            ev = _store_timeline_event(
                session,
                event_type="cashflow_deteriorated",
                title="Cashflow headroom shrank",
                description=(
                    f"Monthly surplus dropped from ${prev_surplus:,.0f} to ${curr_surplus:,.0f}."
                ),
                occurred_at=occurred,
                severity="warning",
                provenance=_provenance(
                    rule="cashflow_deteriorated",
                    before_id=prev.id,
                    after_id=curr.id,
                    evidence={"prev_surplus": prev_surplus, "curr_surplus": curr_surplus},
                ),
                emit_events=emit_events,
            )
            events.append(ev)

        prev_buf = float(prev_m.get("emergency_buffer") or 0)
        curr_buf = float(curr_m.get("emergency_buffer") or 0)
        if curr_buf >= prev_buf + BUFFER_GROW_MIN:
            ev = _store_timeline_event(
                session,
                event_type="emergency_buffer_grew",
                title="Emergency buffer increased",
                description=f"Emergency fund grew by ${curr_buf - prev_buf:,.0f}.",
                occurred_at=occurred,
                severity="info",
                provenance=_provenance(
                    rule="emergency_buffer_grew",
                    before_id=prev.id,
                    after_id=curr.id,
                    evidence={"prev_buffer": prev_buf, "curr_buffer": curr_buf},
                ),
                emit_events=emit_events,
            )
            events.append(ev)

        prev_g = float(prev.grocery_pressure or 0)
        curr_g = float(curr.grocery_pressure or 0)
        if prev_g > 0 and curr_g > prev_g * (1 + GROCERY_SPIKE_MIN_PCT):
            ev = _store_timeline_event(
                session,
                event_type="grocery_spending_spike",
                title="Grocery pressure increased",
                description=f"Estimated monthly grocery spend rose to ${curr_g:,.0f}.",
                occurred_at=occurred,
                severity="warning",
                provenance=_provenance(
                    rule="grocery_spending_spike",
                    before_id=prev.id,
                    after_id=curr.id,
                    evidence={"prev_grocery": prev_g, "curr_grocery": curr_g},
                ),
                emit_events=emit_events,
            )
            events.append(ev)

        prev_p = float(prev.pressure_score or 0)
        curr_p = float(curr.pressure_score or 0)
        if curr_p >= prev_p + PRESSURE_IMPROVE_MIN:
            ev = _store_timeline_event(
                session,
                event_type="forecast_risk_increase",
                title="Forecast pressure increased",
                description=f"Stress pressure score rose from {prev_p:.0f} to {curr_p:.0f}.",
                occurred_at=occurred,
                severity="warning",
                provenance=_provenance(
                    rule="forecast_risk_increase",
                    before_id=prev.id,
                    after_id=curr.id,
                    evidence={"prev_pressure": prev_p, "curr_pressure": curr_p},
                ),
                emit_events=emit_events,
            )
            events.append(ev)
        elif prev_p - curr_p >= PRESSURE_IMPROVE_MIN:
            ev = _store_timeline_event(
                session,
                event_type="forecast_risk_reduction",
                title="Forecast pressure eased",
                description=f"Stress pressure score fell from {prev_p:.0f} to {curr_p:.0f}.",
                occurred_at=occurred,
                severity="info",
                provenance=_provenance(
                    rule="forecast_risk_reduction",
                    before_id=prev.id,
                    after_id=curr.id,
                    evidence={"prev_pressure": prev_p, "curr_pressure": curr_p},
                ),
                emit_events=emit_events,
            )
            events.append(ev)
            if emit_events:
                event_bus.emit(
                    Event(
                        event_type=EventType.FINANCIAL_TREND_IMPROVING.value,
                        source="engine",
                        payload={"metric": "pressure_score", "cadence": cadence},
                    ),
                    session=session,
                )

        try:
            prev_mort = json.loads(prev.mortgage_json or "[]")
            curr_mort = json.loads(curr.mortgage_json or "[]")
        except json.JSONDecodeError:
            prev_mort, curr_mort = [], []

        for before, after in zip(prev_mort, curr_mort, strict=False):
            br = float(before.get("current_rate") or before.get("apr") or 0)
            ar = float(after.get("current_rate") or after.get("apr") or 0)
            if br > 0 and ar > 0 and ar < br - 0.1:
                ev = _store_timeline_event(
                    session,
                    event_type="mortgage_rate_improved",
                    title="Mortgage rate improved",
                    description=f"Rate moved from {br:.2f}% to {ar:.2f}%.",
                    occurred_at=occurred,
                    provenance=_provenance(
                        rule="mortgage_rate_improved",
                        before_id=prev.id,
                        after_id=curr.id,
                        evidence={"before_rate": br, "after_rate": ar},
                    ),
                    emit_events=emit_events,
                )
                events.append(ev)

    return events


def sync_action_timeline_events(session: Session, *, emit_events: bool = True) -> list[FinancialTimelineEvent]:
    """Record completed actions and measurable savings on the timeline."""
    events: list[FinancialTimelineEvent] = []
    actions = list(
        session.exec(
            select(FinancialAction).where(FinancialAction.status == "completed")
        ).all()
    )
    for act in actions:
        occurred = (act.completed_at or act.created_at).date()
        actual_m = float(act.actual_monthly_savings or 0)
        if actual_m > 0:
            title = f"Savings win: {act.title}"
            ev = _store_timeline_event(
                session,
                event_type="savings_win",
                title=title,
                description=f"Verified ${actual_m:,.0f}/mo savings.",
                occurred_at=occurred,
                provenance=_provenance(
                    rule="action_savings_win",
                    evidence={"action_id": act.id, "actual_monthly_savings": actual_m},
                ),
                payload={"action_id": act.id},
                emit_events=emit_events,
            )
            events.append(ev)
        else:
            ev = _store_timeline_event(
                session,
                event_type="action_completed",
                title=f"Completed: {act.title}",
                description=act.notes or "Action marked complete.",
                occurred_at=occurred,
                provenance=_provenance(
                    rule="action_completed",
                    evidence={"action_id": act.id},
                ),
                payload={"action_id": act.id},
                emit_events=emit_events,
            )
            events.append(ev)

        if act.action_type == "cancel_subscription" or "subscription" in (act.title or "").lower():
            _store_timeline_event(
                session,
                event_type="subscription_removed",
                title=f"Subscription removed: {act.title}",
                description="Tracked subscription cancellation.",
                occurred_at=occurred,
                provenance=_provenance(
                    rule="subscription_removed",
                    evidence={"action_id": act.id},
                ),
                emit_events=emit_events,
            )

    return events


def list_timeline_events(session: Session, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = list(
        session.exec(
            select(FinancialTimelineEvent).order_by(
                FinancialTimelineEvent.occurred_at.desc(),  # type: ignore[arg-type]
                FinancialTimelineEvent.created_at.desc(),  # type: ignore[arg-type]
            )
        ).all()
    )[:limit]
    return [_event_to_dict(r) for r in rows]


def _event_to_dict(row: FinancialTimelineEvent) -> dict[str, Any]:
    try:
        provenance = json.loads(row.provenance_json or "{}")
    except json.JSONDecodeError:
        provenance = {}
    try:
        payload = json.loads(row.payload_json or "{}")
    except json.JSONDecodeError:
        payload = {}
    return {
        "id": row.id,
        "event_type": row.event_type,
        "title": row.title,
        "description": row.description,
        "severity": row.severity,
        "occurred_at": row.occurred_at.isoformat(),
        "provenance": provenance,
        "payload": payload,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def pressure_history(session: Session, *, cadence: str = "daily") -> list[dict[str, Any]]:
    rows = list_snapshots(session, cadence=cadence, limit=90)
    rows = sorted(rows, key=lambda r: r.snapshot_date)
    return [
        {
            "date": r.snapshot_date.isoformat(),
            "pressure_score": r.pressure_score,
            "data_quality_score": r.data_quality_score,
            "grocery_pressure": r.grocery_pressure,
        }
        for r in rows
    ]


def cashflow_trend(session: Session, *, cadence: str = "daily") -> list[dict[str, Any]]:
    rows = list_snapshots(session, cadence=cadence, limit=90)
    rows = sorted(rows, key=lambda r: r.snapshot_date)
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            cf = json.loads(r.cashflow_json or "{}")
        except json.JSONDecodeError:
            cf = {}
        m = _load_metrics(r)
        out.append({
            "date": r.snapshot_date.isoformat(),
            "monthly_surplus": m.get("monthly_surplus", cf.get("monthly_surplus")),
            "risk_level": cf.get("risk_level"),
            "safe_to_spend": cf.get("safe_to_spend"),
        })
    return out


def debt_milestones(session: Session) -> list[dict[str, Any]]:
    """Debt payoff milestones from snapshot history."""
    rows = list_snapshots(session, cadence="monthly", limit=24)
    if not rows:
        rows = list_snapshots(session, cadence="weekly", limit=52)
    rows = sorted(rows, key=lambda r: r.snapshot_date)
    milestones: list[dict[str, Any]] = []
    thresholds = (25000, 20000, 15000, 10000, 5000, 0)
    crossed: set[int] = set()
    for r in rows:
        total = float(_load_metrics(r).get("total_debt") or 0)
        for t in thresholds:
            if t not in crossed and total <= t:
                crossed.add(t)
                milestones.append({
                    "date": r.snapshot_date.isoformat(),
                    "total_debt": total,
                    "milestone": f"debt_under_{t}" if t > 0 else "debt_paid_off",
                    "provenance": {"snapshot_id": r.id, "rule": "debt_milestone"},
                })
    return milestones


def build_timeline_dashboard(session: Session) -> dict[str, Any]:
    """Aggregate payload for /financial-timeline UI."""
    sync_action_timeline_events(session, emit_events=False)
    detect_events_from_snapshots(session, emit_events=False)
    snapshots = list_snapshots(session, limit=30)
    return {
        "snapshot_count": len(snapshots),
        "min_snapshots_for_trend": MIN_SNAPSHOTS_FOR_TREND,
        "events": list_timeline_events(session, limit=50),
        "pressure_history": pressure_history(session),
        "cashflow_trend": cashflow_trend(session),
        "debt_milestones": debt_milestones(session),
        "completed_actions": _completed_actions_summary(session),
        "savings_wins": [e for e in list_timeline_events(session, limit=50) if e["event_type"] == "savings_win"],
    }


def _completed_actions_summary(session: Session) -> list[dict[str, Any]]:
    acts = list(
        session.exec(
            select(FinancialAction)
            .where(FinancialAction.status == "completed")
            .order_by(FinancialAction.completed_at.desc())  # type: ignore[arg-type]
        ).all()
    )[:20]
    return [
        {
            "id": a.id,
            "title": a.title,
            "completed_at": a.completed_at.isoformat() if a.completed_at else None,
            "actual_monthly_savings": a.actual_monthly_savings,
            "action_type": a.action_type,
        }
        for a in acts
    ]


def seed_snapshots_for_tests(
    session: Session,
    *,
    rows: list[dict[str, Any]],
) -> list[FinancialSnapshotRecord]:
    """Insert historical snapshots (tests only)."""
    stored: list[FinancialSnapshotRecord] = []
    for spec in rows:
        snap_date = spec.get("snapshot_date")
        if isinstance(snap_date, str):
            snap_date = date.fromisoformat(snap_date)
        row = FinancialSnapshotRecord(
            cadence=spec.get("cadence", "daily"),
            snapshot_date=snap_date or _today(),
            cashflow_json=json.dumps(spec.get("cashflow", {})),
            debts_json=json.dumps(spec.get("debts", {})),
            dti_json=json.dumps(spec.get("dti", {})),
            credit_util_json=json.dumps(spec.get("credit_util", {})),
            mortgage_json=json.dumps(spec.get("mortgage", [])),
            grocery_pressure=float(spec.get("grocery_pressure", 0)),
            recommendation_count=int(spec.get("recommendation_count", 0)),
            pressure_score=float(spec.get("pressure_score", 0)),
            data_quality_score=float(spec.get("data_quality_score", 0)),
            metrics_json=json.dumps(spec.get("metrics", spec.get("metrics_json", {}))),
            source_snapshot_id=spec.get("source_snapshot_id", ""),
        )
        session.add(row)
        stored.append(row)
    session.commit()
    for row in stored:
        session.refresh(row)
    return stored
