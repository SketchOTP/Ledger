"""D004 closed-loop tests: observations are factual, bounded, and auditable."""

import json

import pytest
from sqlmodel import select

from app.core.v2_opportunities import create_candidate_action, ingest_candidate, ranked_candidates, transition_candidate
from app.core.v2_outcome_learning import (
    MIN_SAMPLE_SIZE,
    calibration_for_candidate,
    learning_payload,
    record_candidate_outcome,
    research_targeting,
)
from app.db.models import OpportunityCalibrationAudit, OpportunityPredictionSnapshot


def _candidate(session, title="Closed loop candidate", category="digital_product", expected_net=(100, 200)):
    candidate = ingest_candidate(
        session,
        {
            "title": title,
            "category": category,
            "source_type": "owner_supplied",
            "expected_net_low": expected_net[0],
            "expected_net_high": expected_net[1],
            "probability_low": 0.5,
            "probability_high": 0.7,
            "scalability": 0.8,
            "repeatability": 0.8,
            "automation_potential": 0.8,
            "reversibility": 0.8,
        },
    )
    for state in ("researched", "qualified", "proposed", "accepted"):
        transition_candidate(session, candidate, state, "test")
    candidate.safety_eligible = True
    candidate.qualification_status = "qualified"
    session.add(candidate)
    session.commit()
    return candidate


def test_prediction_is_frozen_and_missing_actuals_stay_unknown(session):
    candidate = _candidate(session)
    snapshot = session.exec(select(OpportunityPredictionSnapshot)).one()
    before = json.loads(snapshot.prediction_json)
    candidate.expected_net_low = 9_999
    session.add(candidate)
    session.commit()
    action = create_candidate_action(session, candidate)
    outcome = record_candidate_outcome(
        session,
        candidate,
        {
            "financial_action_id": action.id,
            "outcome_status": "abandoned",
            "outcome_reason": "no time",
            "actual_time_to_value_days": 4,
        },
    )
    assert json.loads(snapshot.prediction_json) == before
    assert outcome.actual_net_value is None
    assert outcome.actual_setup_hours is None
    assert outcome.actual_time_to_value_days == 4
    assert candidate.lifecycle_status == "outcome_recorded"


def test_sparse_then_sufficient_observations_have_bounded_auditable_calibration(session):
    first = _candidate(session, "Positive 0")
    action = create_candidate_action(session, first)
    record_candidate_outcome(
        session,
        first,
        {
            "financial_action_id": action.id,
            "outcome_status": "completed",
            "actual_net_value": 190,
            "actual_setup_hours": 1.5,
        },
    )
    assert calibration_for_candidate(session, first)["adjustment"] == 0

    for index in range(1, MIN_SAMPLE_SIZE):
        candidate = _candidate(session, f"Positive {index}")
        action = create_candidate_action(session, candidate)
        record_candidate_outcome(
            session,
            candidate,
            {"financial_action_id": action.id, "outcome_status": "completed", "actual_net_value": 190},
        )
    calibrated = calibration_for_candidate(session, first)
    assert 0 < calibrated["adjustment"] <= 10
    assert calibrated["sample_size"] >= MIN_SAMPLE_SIZE
    assert session.exec(select(OpportunityCalibrationAudit)).all()
    assert learning_payload(session)["research_targeting"]["exploration_floor"] == 0.2
    assert research_targeting(session)["categories"][0]["weight"] >= 0.2


def test_safety_remains_hard_gate_despite_positive_learning(session):
    for index in range(MIN_SAMPLE_SIZE):
        candidate = _candidate(session, f"Learn {index}")
        action = create_candidate_action(session, candidate)
        record_candidate_outcome(
            session,
            candidate,
            {"financial_action_id": action.id, "outcome_status": "completed", "actual_net_value": 200},
        )
    unsafe = _candidate(session, "Unsafe", category="digital_product")
    unsafe.safety_eligible = False
    session.add(unsafe)
    session.commit()
    assert unsafe.id not in {row["id"] for row in ranked_candidates(session)}


@pytest.mark.parametrize(
    ("actual", "expected_adjustment_sign"),
    [(300, "positive"), (150, "weaker"), (0, "negative"), (-50, "negative")],
)
def test_calibration_is_value_aware_for_completed_outcomes(session, actual, expected_adjustment_sign):
    first = _candidate(session, "Value-aware 0", expected_net=(300, 300))
    first_action = create_candidate_action(session, first)
    record_candidate_outcome(
        session,
        first,
        {"financial_action_id": first_action.id, "outcome_status": "completed", "actual_net_value": actual},
    )
    for index in range(1, MIN_SAMPLE_SIZE):
        candidate = _candidate(session, f"Value-aware {index}", expected_net=(300, 300))
        action = create_candidate_action(session, candidate)
        record_candidate_outcome(
            session,
            candidate,
            {"financial_action_id": action.id, "outcome_status": "completed", "actual_net_value": actual},
        )

    profile = calibration_for_candidate(session, first)
    if expected_adjustment_sign == "positive":
        assert profile["adjustment"] > 0
    elif expected_adjustment_sign == "weaker":
        assert profile["adjustment"] < 3
    else:
        assert profile["adjustment"] < 0
    assert -10 <= profile["adjustment"] <= 10
    assert profile["profile"]["financial_success_rate"] == (1.0 if actual > 0 else 0.0)


def test_completed_unknown_value_is_not_financial_success(session):
    first = _candidate(session, "Unknown value 0", expected_net=(300, 300))
    action = create_candidate_action(session, first)
    record_candidate_outcome(
        session,
        first,
        {"financial_action_id": action.id, "outcome_status": "completed", "actual_net_value": None},
    )
    for index in range(1, MIN_SAMPLE_SIZE):
        candidate = _candidate(session, f"Unknown value {index}", expected_net=(300, 300))
        action = create_candidate_action(session, candidate)
        record_candidate_outcome(
            session,
            candidate,
            {"financial_action_id": action.id, "outcome_status": "completed", "actual_net_value": None},
        )

    profile = calibration_for_candidate(session, first)["profile"]
    assert profile["completion_rate"] == 1.0
    assert profile["execution_sample_size"] == MIN_SAMPLE_SIZE
    assert profile["financial_value_sample_size"] == 0
    assert profile["financial_success_rate"] is None
    assert profile["value_realization_ratio"] is None
    assert profile["score_adjustment"] == 0


def test_mixed_unknown_and_known_values_use_financial_denominator_only(session):
    first = _candidate(session, "Mixed value 0", expected_net=(300, 300))
    action = create_candidate_action(session, first)
    record_candidate_outcome(
        session, first, {"financial_action_id": action.id, "outcome_status": "completed", "actual_net_value": None}
    )
    for index in range(1, 3):
        candidate = _candidate(session, f"Mixed unknown {index}", expected_net=(300, 300))
        action = create_candidate_action(session, candidate)
        record_candidate_outcome(
            session,
            candidate,
            {"financial_action_id": action.id, "outcome_status": "completed", "actual_net_value": None},
        )
    for index in range(3, MIN_SAMPLE_SIZE):
        candidate = _candidate(session, f"Mixed known {index}", expected_net=(300, 300))
        action = create_candidate_action(session, candidate)
        record_candidate_outcome(
            session,
            candidate,
            {"financial_action_id": action.id, "outcome_status": "completed", "actual_net_value": 300},
        )

    profile = calibration_for_candidate(session, first)["profile"]
    assert profile["execution_sample_size"] == MIN_SAMPLE_SIZE
    assert profile["financial_value_sample_size"] == 2
    assert profile["financial_success_rate"] == 1.0
    assert profile["value_realization_ratio"] == 1.0
    assert profile["financial_adjustment"] == 0
    assert profile["realization_adjustment"] == 0
    assert profile["score_adjustment"] == 0


def test_unknown_values_cannot_amplify_one_positive_observation(session):
    first = _candidate(session, "Extreme unknown 0", expected_net=(300, 300))
    action = create_candidate_action(session, first)
    record_candidate_outcome(
        session, first, {"financial_action_id": action.id, "outcome_status": "completed", "actual_net_value": 300}
    )
    for index in range(1, 10):
        candidate = _candidate(session, f"Extreme unknown {index}", expected_net=(300, 300))
        action = create_candidate_action(session, candidate)
        record_candidate_outcome(
            session,
            candidate,
            {"financial_action_id": action.id, "outcome_status": "completed", "actual_net_value": None},
        )

    profile = calibration_for_candidate(session, first)["profile"]
    assert profile["execution_sample_size"] == 10
    assert profile["financial_value_sample_size"] == 1
    assert profile["realization_sample_size"] == 1
    assert profile["financial_confidence"] == 0.1
    assert profile["realization_confidence"] == 0.1
    assert profile["score_adjustment"] == 0


def test_financial_signal_requires_its_own_minimum_sample(session):
    for index in range(4):
        candidate = _candidate(session, f"Threshold four {index}", expected_net=(300, 300))
        action = create_candidate_action(session, candidate)
        record_candidate_outcome(
            session,
            candidate,
            {"financial_action_id": action.id, "outcome_status": "completed", "actual_net_value": 300},
        )
    first = _candidate(session, "Threshold five", expected_net=(300, 300))
    action = create_candidate_action(session, first)
    record_candidate_outcome(
        session, first, {"financial_action_id": action.id, "outcome_status": "completed", "actual_net_value": 300}
    )
    profile = calibration_for_candidate(session, first)["profile"]
    assert profile["financial_value_sample_size"] == 5
    assert profile["financial_adjustment"] > 0


def test_realization_signal_requires_valid_prediction_sample(session):
    for index in range(2):
        candidate = _candidate(session, f"Realization known {index}", expected_net=(300, 300))
        action = create_candidate_action(session, candidate)
        record_candidate_outcome(
            session,
            candidate,
            {"financial_action_id": action.id, "outcome_status": "completed", "actual_net_value": 150},
        )
    for index in range(2, MIN_SAMPLE_SIZE):
        candidate = _candidate(session, f"Realization missing {index}", expected_net=(None, None))
        action = create_candidate_action(session, candidate)
        record_candidate_outcome(
            session,
            candidate,
            {"financial_action_id": action.id, "outcome_status": "completed", "actual_net_value": 150},
        )

    profile = calibration_for_candidate(session, candidate)["profile"]
    assert profile["financial_value_sample_size"] == MIN_SAMPLE_SIZE
    assert profile["realization_sample_size"] == 2
    assert profile["financial_adjustment"] > 0
    assert profile["realization_adjustment"] == 0


def test_failed_or_abandoned_unknown_values_are_execution_adverse_only(session):
    first = _candidate(session, "Unknown failed 0", expected_net=(300, 300))
    action = create_candidate_action(session, first)
    record_candidate_outcome(
        session, first, {"financial_action_id": action.id, "outcome_status": "failed", "actual_net_value": None}
    )
    for index in range(1, MIN_SAMPLE_SIZE):
        candidate = _candidate(session, f"Unknown failed {index}", expected_net=(300, 300))
        action = create_candidate_action(session, candidate)
        status = "abandoned" if index % 2 else "failed"
        record_candidate_outcome(
            session, candidate, {"financial_action_id": action.id, "outcome_status": status, "actual_net_value": None}
        )

    profile = calibration_for_candidate(session, first)["profile"]
    assert profile["execution_sample_size"] == MIN_SAMPLE_SIZE
    assert profile["financial_value_sample_size"] == 0
    assert profile["financial_success_rate"] is None
    assert profile["score_adjustment"] < 0


def test_owner_dismissal_is_distinct_from_automatic_rejection(session):
    dismissed = _candidate(session, "Not now")
    transition_candidate(session, dismissed, "dismissed", "owner_timing")
    automatic = ingest_candidate(session, {"title": "Unsafe automatic", "expected_net_low": 1, "expected_net_high": 2})
    transition_candidate(session, automatic, "rejected", "required_capital_exceeds_deployable_surplus")
    payload = learning_payload(session)
    assert payload["owner_dismissal_count"] == 1
