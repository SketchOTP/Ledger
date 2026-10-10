"""Closed-loop, bounded learning for V2 opportunity candidates.

This module intentionally consumes observed decisions and outcomes only.  It
cannot qualify a candidate or modify D001 money-state safety.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from statistics import mean
from typing import Any

from sqlmodel import Session, select

from app.db.models import (
    FinancialAction,
    OpportunityCalibrationAudit,
    OpportunityCandidate,
    OpportunityOutcomeObservation,
    OpportunityPredictionSnapshot,
    OpportunityTransition,
)

LEARNING_VERSION = "v2.4"
MIN_SAMPLE_SIZE = 5
FULL_CONFIDENCE_SAMPLE = 10
MAX_SCORE_ADJUSTMENT = 10.0
TERMINAL_OUTCOMES = frozenset({"completed", "failed", "abandoned"})
OWNER_DISMISSAL_REASONS = frozenset(
    {"owner_irrelevant", "owner_too_much_work", "owner_timing", "owner_capital", "owner_weak_evidence", "owner_other"}
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _loads(raw: str) -> Any:
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}


def _prediction(candidate: OpportunityCandidate) -> dict[str, Any]:
    return {
        "candidate_id": candidate.id,
        "category": candidate.category,
        "source_type": candidate.source_type,
        "source_label": candidate.source_label,
        "score": candidate.rank_score,
        "score_components": _loads(candidate.score_components_json),
        "expected_gross_low": candidate.expected_gross_low,
        "expected_gross_high": candidate.expected_gross_high,
        "expected_net_low": candidate.expected_net_low,
        "expected_net_high": candidate.expected_net_high,
        "probability_low": candidate.probability_low,
        "probability_high": candidate.probability_high,
        "setup_hours": candidate.setup_hours,
        "recurring_hours_per_week": candidate.recurring_hours_per_week,
        "time_to_value_days": candidate.time_to_value_days,
        "required_capital": candidate.required_capital,
        "capital_at_risk": candidate.capital_at_risk,
        "downside_severity": candidate.downside_severity,
        "verification_status": candidate.verification_status,
        "freshness_status": candidate.freshness_status,
        "capability_fit_score": candidate.capability_fit_score,
        "matched_skills": _loads(candidate.matched_skills_json),
        "matched_assets": _loads(candidate.matched_assets_json),
    }


def freeze_prediction(session: Session, candidate: OpportunityCandidate) -> OpportunityPredictionSnapshot:
    """Freeze once at acceptance. Later candidate edits never rewrite it."""
    row = session.exec(
        select(OpportunityPredictionSnapshot).where(OpportunityPredictionSnapshot.candidate_id == candidate.id)
    ).first()
    if row:
        return row
    row = OpportunityPredictionSnapshot(
        candidate_id=candidate.id,
        scoring_version=candidate.scoring_version,
        prediction_json=json.dumps(_prediction(candidate), sort_keys=True, default=str),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def link_prediction_to_action(
    session: Session, candidate: OpportunityCandidate, action: FinancialAction
) -> OpportunityPredictionSnapshot:
    row = freeze_prediction(session, candidate)
    if row.financial_action_id is None:
        row.financial_action_id = action.id
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _non_negative(data: dict[str, Any], key: str) -> float | None:
    value = data.get(key)
    if value is None:
        return None
    numeric = float(value)
    if numeric < 0 and key != "actual_net_value":
        raise ValueError(f"{key} must be non-negative")
    return numeric


def record_candidate_outcome(
    session: Session,
    candidate: OpportunityCandidate,
    data: dict[str, Any],
) -> OpportunityOutcomeObservation:
    """Persist observed facts without converting unknowns into zeroes."""
    status = str(data.get("outcome_status") or "").strip()
    if status not in {"in_progress", *TERMINAL_OUTCOMES}:
        raise ValueError("outcome_status must be in_progress, completed, failed, or abandoned")
    action_id = data.get("financial_action_id")
    action = session.get(FinancialAction, action_id) if action_id else None
    if action and action.opportunity_candidate_id != candidate.id:
        raise ValueError("financial action is not linked to candidate")
    if candidate.lifecycle_status not in {"accepted", "attempted", "outcome_recorded"}:
        raise ValueError("outcomes require an accepted or attempted candidate")
    existing = session.exec(
        select(OpportunityOutcomeObservation).where(
            OpportunityOutcomeObservation.candidate_id == candidate.id,
            OpportunityOutcomeObservation.financial_action_id == (action.id if action else None),
        )
    ).first()
    row = existing or OpportunityOutcomeObservation(
        candidate_id=candidate.id, financial_action_id=action.id if action else None
    )
    row.outcome_status = status
    for key in (
        "actual_gross_value",
        "actual_net_value",
        "actual_direct_cost",
        "actual_capital_committed",
        "actual_capital_lost",
        "actual_capital_returned",
        "actual_setup_hours",
        "actual_recurring_hours_per_week",
    ):
        if key in data:
            setattr(row, key, _non_negative(data, key))
    if "actual_time_to_value_days" in data:
        days = int(data["actual_time_to_value_days"])
        if days < 0:
            raise ValueError("actual_time_to_value_days must be non-negative")
        row.actual_time_to_value_days = days
    if "first_value_date" in data:
        row.first_value_date = _parse_date(data["first_value_date"])
        if row.actual_time_to_value_days is None:
            snapshot = session.exec(
                select(OpportunityPredictionSnapshot).where(OpportunityPredictionSnapshot.candidate_id == candidate.id)
            ).first()
            if snapshot:
                row.actual_time_to_value_days = max(0, (row.first_value_date - snapshot.created_at.date()).days)
    row.outcome_reason = str(data.get("outcome_reason") or row.outcome_reason or "")[:500]
    row.outcome_notes = str(data.get("outcome_notes") or row.outcome_notes or "")[:4000]
    row.evidence_reference = str(data.get("evidence_reference") or row.evidence_reference or "")[:1000]
    row.completed_at = _now() if status in TERMINAL_OUTCOMES else None
    row.updated_at = _now()
    session.add(row)
    session.commit()
    session.refresh(row)

    # Keep D002's existing actual-field surface useful, but only copy values
    # actually supplied by the owner.
    for target, source in (
        ("actual_net_value", "actual_net_value"),
        ("actual_setup_hours", "actual_setup_hours"),
        ("actual_recurring_hours_per_week", "actual_recurring_hours_per_week"),
        ("actual_time_to_value_days", "actual_time_to_value_days"),
        ("actual_capital_lost", "actual_capital_lost"),
        ("actual_capital_returned", "actual_capital_returned"),
    ):
        value = getattr(row, source)
        if value is not None:
            setattr(candidate, target, value)
    candidate.outcome_failure_reason = row.outcome_reason if status != "completed" else ""
    candidate.outcome_notes = row.outcome_notes
    candidate.updated_at = _now()
    session.add(candidate)
    if status in TERMINAL_OUTCOMES and candidate.lifecycle_status != "outcome_recorded":
        session.add(
            OpportunityTransition(
                candidate_id=candidate.id,
                from_status=candidate.lifecycle_status,
                to_status="outcome_recorded",
                reason=f"outcome:{status}",
            )
        )
        candidate.lifecycle_status = "outcome_recorded"
    session.commit()
    refresh_calibration(session)
    return row


def _expected_net(snapshot: OpportunityPredictionSnapshot) -> float | None:
    data = _loads(snapshot.prediction_json)
    low, high = data.get("expected_net_low"), data.get("expected_net_high")
    return (float(low) + float(high)) / 2 if low is not None and high is not None else None


def _outcome_rows(
    session: Session,
) -> list[tuple[OpportunityOutcomeObservation, OpportunityCandidate, OpportunityPredictionSnapshot | None]]:
    snapshots = {row.candidate_id: row for row in session.exec(select(OpportunityPredictionSnapshot)).all()}
    candidates = {row.id: row for row in session.exec(select(OpportunityCandidate)).all()}
    return [
        (outcome, candidates[outcome.candidate_id], snapshots.get(outcome.candidate_id))
        for outcome in session.exec(select(OpportunityOutcomeObservation)).all()
        if outcome.outcome_status in TERMINAL_OUTCOMES and outcome.candidate_id in candidates
    ]


def _profile(
    rows: list[tuple[OpportunityOutcomeObservation, OpportunityCandidate, OpportunityPredictionSnapshot | None]],
    value: str,
) -> dict[str, Any]:
    # Keep execution and financial-value samples separate.  A terminal row
    # with an unknown net result is still execution evidence, but it is not
    # financial evidence and must not be treated as a failure.
    n = len(rows)
    completed = [r for r in rows if r[0].outcome_status == "completed"]
    abandoned = [r for r in rows if r[0].outcome_status == "abandoned"]
    financial_rows = [r for r in rows if r[0].actual_net_value is not None]
    financial_n = len(financial_rows)
    ratios = [
        float(outcome.actual_net_value) / expected
        for outcome, _, snapshot in rows
        if snapshot
        and outcome.actual_net_value is not None
        and (expected := _expected_net(snapshot))
        and expected > 0
    ]
    # Completion records execution, not financial success. Only a terminal
    # outcome with a strictly positive observed net is a financial success;
    # zero and negative values are observed adverse value, while unknown
    # values are excluded from the financial denominator entirely.
    financial_successes = [
        outcome for outcome, _, _ in financial_rows if outcome.actual_net_value > 0
    ]
    completion_rate = len(completed) / n if n else 0.0
    financial_success_rate = len(financial_successes) / financial_n if financial_n else None
    realization = mean(ratios) if ratios else None
    realization_n = len(ratios)
    execution_confidence = min(1.0, n / FULL_CONFIDENCE_SAMPLE)
    financial_confidence = min(1.0, financial_n / FULL_CONFIDENCE_SAMPLE)
    realization_confidence = min(1.0, realization_n / FULL_CONFIDENCE_SAMPLE)
    # Each signal has its own evidence gate and confidence. Unknown completed
    # outcomes therefore cannot satisfy or amplify financial/value learning.
    execution_failures = len(rows) - len(completed)
    execution_signal = -(execution_failures / n) * 6.0 if n else 0.0
    execution_adjustment = execution_signal * execution_confidence if n >= MIN_SAMPLE_SIZE else 0.0
    financial_signal = (
        (financial_success_rate - 0.5) * 12.0
        if financial_success_rate is not None and financial_n >= MIN_SAMPLE_SIZE
        else 0.0
    )
    financial_adjustment = financial_signal * financial_confidence
    realization_signal = (
        (realization - 1.0) * 5.0 if realization is not None and realization_n >= MIN_SAMPLE_SIZE else 0.0
    )
    realization_adjustment = realization_signal * realization_confidence
    adjustment = max(
        -MAX_SCORE_ADJUSTMENT,
        min(MAX_SCORE_ADJUSTMENT, execution_adjustment + financial_adjustment + realization_adjustment),
    )
    return {
        "value": value,
        "sample_size": n,
        "execution_sample_size": n,
        "financial_value_sample_size": financial_n,
        "completion_rate": round(completion_rate, 3),
        "success_rate": round(financial_success_rate, 3) if financial_success_rate is not None else None,
        "financial_success_rate": round(financial_success_rate, 3) if financial_success_rate is not None else None,
        "abandonment_rate": round(len(abandoned) / n, 3) if n else 0.0,
        "value_realization_ratio": round(realization, 3) if realization is not None else None,
        "execution_confidence": round(execution_confidence, 3),
        "financial_confidence": round(financial_confidence, 3),
        "realization_sample_size": realization_n,
        "realization_confidence": round(realization_confidence, 3),
        "execution_adjustment": round(execution_adjustment, 2),
        "financial_adjustment": round(financial_adjustment, 2),
        "realization_adjustment": round(realization_adjustment, 2),
        "confidence": round(execution_confidence, 3),
        "score_adjustment": round(adjustment, 2),
        "meets_threshold": n >= MIN_SAMPLE_SIZE,
    }


def refresh_calibration(session: Session) -> list[dict[str, Any]]:
    rows = _outcome_rows(session)
    groups: dict[
        tuple[str, str],
        list[tuple[OpportunityOutcomeObservation, OpportunityCandidate, OpportunityPredictionSnapshot | None]],
    ] = {}
    for row in rows:
        candidate = row[1]
        groups.setdefault(("category", candidate.category or "other"), []).append(row)
        groups.setdefault(("provider", candidate.source_label or candidate.source_type or "unknown"), []).append(row)
    saved = []
    for (dimension, value), group in groups.items():
        profile = _profile(group, value)
        entry = OpportunityCalibrationAudit(
            dimension=dimension,
            dimension_value=value,
            sample_size=profile["sample_size"],
            confidence=profile["confidence"],
            score_adjustment=profile["score_adjustment"],
            learning_version=LEARNING_VERSION,
            profile_json=json.dumps(profile, sort_keys=True),
            reason=f"observed_outcomes={profile['sample_size']} financial_success={profile['success_rate']}",
        )
        session.add(entry)
        saved.append(profile | {"dimension": dimension})
    session.commit()
    return saved


def calibration_for_candidate(session: Session, candidate: OpportunityCandidate) -> dict[str, Any]:
    rows = _outcome_rows(session)
    group = [row for row in rows if row[1].category == candidate.category]
    profile = _profile(group, candidate.category) if group else _profile([], candidate.category)
    return {
        "base_score": float(candidate.rank_score or 0),
        "adjustment": profile["score_adjustment"],
        "final_score": round(max(0.0, min(100.0, float(candidate.rank_score or 0) + profile["score_adjustment"])), 2),
        "reason": "category observed outcomes" if group else "no observed category outcomes",
        "sample_size": profile["sample_size"],
        "confidence": profile["confidence"],
        "learning_version": LEARNING_VERSION,
        "profile": profile,
    }


def apply_calibration(session: Session, candidate: OpportunityCandidate, components: dict[str, Any]) -> dict[str, Any]:
    data = calibration_for_candidate(session, candidate)
    base = float(components["score"])
    data["base_score"] = base
    data["final_score"] = round(max(0.0, min(100.0, base + data["adjustment"])), 2)
    components = dict(components)
    components.update(
        {"base_score": base, "calibration": data, "score": data["final_score"], "version": LEARNING_VERSION}
    )
    return components


def research_targeting(session: Session) -> dict[str, Any]:
    rows = _outcome_rows(session)
    by_category: dict[
        str, list[tuple[OpportunityOutcomeObservation, OpportunityCandidate, OpportunityPredictionSnapshot | None]]
    ] = {}
    for row in rows:
        by_category.setdefault(row[1].category or "other", []).append(row)
    profiles = [_profile(group, category) for category, group in by_category.items()]
    # Exploration floor means a negative result only shifts bounded budget; it
    # cannot erase a category without an explicit owner refusal.
    priorities = sorted(
        [
            {"category": p["value"], "weight": round(0.2 + max(0.0, p["score_adjustment"]) / 12 * 0.8, 3), **p}
            for p in profiles
        ],
        key=lambda p: p["weight"],
        reverse=True,
    )
    return {"learning_version": LEARNING_VERSION, "exploration_floor": 0.2, "categories": priorities}


def material_state_fingerprint(session: Session, brief: dict[str, Any]) -> str:
    latest_outcome = max(
        (row.updated_at for row in session.exec(select(OpportunityOutcomeObservation)).all()), default=None
    )
    payload = {
        "money": brief.get("financial_constraints", {}),
        "capability": brief.get("capability", {}),
        "candidate_states": sorted(
            (row.category, row.lifecycle_status, row.freshness_status)
            for row in session.exec(select(OpportunityCandidate)).all()
        ),
        "latest_outcome": latest_outcome.isoformat() if latest_outcome else None,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:24]


def learning_payload(session: Session) -> dict[str, Any]:
    rows = _outcome_rows(session)
    audits = session.exec(select(OpportunityCalibrationAudit)).all()
    transitions = session.exec(select(OpportunityTransition)).all()
    owner_dismissals = [
        t
        for t in transitions
        if t.to_status == "dismissed" and (t.reason in OWNER_DISMISSAL_REASONS or t.reason.startswith("owner_other:"))
    ]
    return {
        "learning_version": LEARNING_VERSION,
        "minimum_sample_size": MIN_SAMPLE_SIZE,
        "max_score_adjustment": MAX_SCORE_ADJUSTMENT,
        "outcome_count": len(rows),
        "owner_dismissal_count": len(owner_dismissals),
        "research_targeting": research_targeting(session),
        "audits": [
            {
                "dimension": a.dimension,
                "value": a.dimension_value,
                "sample_size": a.sample_size,
                "execution_sample_size": _loads(a.profile_json).get("execution_sample_size", a.sample_size),
                "financial_value_sample_size": _loads(a.profile_json).get("financial_value_sample_size", 0),
                "confidence": a.confidence,
                "adjustment": a.score_adjustment,
                "reason": a.reason,
                "version": a.learning_version,
            }
            for a in audits[-50:]
        ],
    }
