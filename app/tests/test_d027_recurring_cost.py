"""D027 recurring-cost detection, confirmation, and live safety guards."""

import json
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlmodel import select

from app.core.financial_snapshot import detect_recurring_subscriptions
from app.core.opportunity_engine import _subscription_opportunities
from app.core.v2_opportunities import (
    normalize_v1_opportunity,
    observe_recurring_outcomes,
    ranked_candidates,
    realized_value_summary,
    respond_to_recurring_candidate,
    sync_household_recurring_cost_moves,
)
from app.core.v2_validation import save_fact
from app.db.models import Bill, FinancialAction, OpportunityCandidate, OpportunityOutcomeObservation, Transaction

CADENCE_DAYS = {
    "weekly": 7,
    "biweekly": 14,
    "monthly": 30.4375,
    "quarterly": 91.3125,
    "semiannual": 182.625,
    "annual": 365.25,
}


def _tx(session, description, amount, dates, category="entertainment"):
    session.add_all(
        [
            Transaction(description=description, category=category, amount=amount, transaction_date=when)
            for when in dates
        ]
    )
    session.commit()


def _recurring_candidate(session, cadence, amount, *, last_date="2026-03-01", evidence=None):
    cadence_days = CADENCE_DAYS
    annual_counts = {
        "weekly": 365.25 / 7,
        "biweekly": 365.25 / 14,
        "monthly": 12,
        "quarterly": 4,
        "semiannual": 2,
        "annual": 1,
    }
    rows = evidence or [
        {
            "math": {
                "merchant_key": "service",
                "cadence": cadence,
                "cadence_days": cadence_days[cadence],
                "avg_amount": amount,
                "estimated_annual_cost": amount * annual_counts[cadence],
                "last_transaction_date": last_date,
                "first_transaction_date": last_date,
            }
        }
    ]
    candidate = OpportunityCandidate(
        identity_key=f"recurring:{cadence}:{amount}",
        title="Review recurring charge",
        category="subscription",
        source_type="deterministic",
        evidence_json=json.dumps(rows),
    )
    session.add(candidate)
    session.commit()
    return candidate


def _action(session, candidate, days_ago):
    action = FinancialAction(
        opportunity_candidate_id=candidate.id,
        title="Cancel recurring charge",
        action_type="cancel_subscription",
        created_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days_ago),
    )
    session.add(action)
    session.commit()
    return action


def test_cadence_annualization_and_descriptor_normalization(session):
    _tx(session, "POS Netflix REF-12345", 20, [date(2026, 1, 1), date(2026, 2, 2), date(2026, 3, 2)])
    _tx(session, "Annual cloud renewal", 120, [date(2025, 1, 1), date(2026, 1, 3)])
    patterns = detect_recurring_subscriptions(session)
    netflix = next(row for row in patterns if row["merchant_key"] == "netflix")
    annual = next(row for row in patterns if row["merchant_key"] == "annual cloud renewal")
    assert netflix["cadence"] == "monthly" and netflix["estimated_annual_cost"] == 240
    assert annual["cadence"] == "annual" and annual["estimated_annual_cost"] == 120


@pytest.mark.parametrize(
    ("cadence", "amount", "expected"),
    [
        ("weekly", 25, round(25 * 365.25 / 7, 2)),
        ("biweekly", 25, round(25 * 365.25 / 14, 2)),
        ("monthly", 25, 300),
        ("quarterly", 100, 400),
        ("semiannual", 240, 480),
        ("annual", 120, 120),
    ],
)
def test_confirmed_recurring_economics_use_cadence(session, cadence, amount, expected):
    candidate = _recurring_candidate(session, cadence, amount)
    save_fact(session, candidate, fact_type="can_cancel", value=True)
    session.refresh(candidate)
    assert candidate.expected_gross_low == expected
    assert candidate.expected_net_low == expected
    assert candidate.time_to_value_days == round(
        CADENCE_DAYS[cadence]
    )


def test_latest_recurrence_observation_supersedes_old_price(session):
    candidate = _recurring_candidate(
        session,
        "monthly",
        20,
        evidence=[
            {
                "math": {
                    "merchant_key": "service",
                    "cadence": "monthly",
                    "cadence_days": 30.4375,
                    "avg_amount": 20,
                    "estimated_annual_cost": 240,
                    "last_transaction_date": "2026-02-01",
                }
            },
            {
                "math": {
                    "merchant_key": "service",
                    "cadence": "monthly",
                    "cadence_days": 30.4375,
                    "avg_amount": 25,
                    "estimated_annual_cost": 300,
                    "last_transaction_date": "2026-03-01",
                }
            },
        ],
    )
    save_fact(session, candidate, fact_type="can_cancel", value=True)
    session.refresh(candidate)
    assert candidate.expected_gross_low == 300
    assert len(json.loads(candidate.evidence_json)) == 2


def test_latest_recurrence_observation_supersedes_cadence(session):
    candidate = _recurring_candidate(
        session,
        "monthly",
        20,
        evidence=[
            {
                "math": {
                    "merchant_key": "service",
                    "cadence": "monthly",
                    "cadence_days": 30.4375,
                    "avg_amount": 20,
                    "estimated_annual_cost": 240,
                    "last_transaction_date": "2026-02-01",
                }
            },
            {
                "math": {
                    "merchant_key": "service",
                    "cadence": "quarterly",
                    "cadence_days": 91.3125,
                    "avg_amount": 100,
                    "estimated_annual_cost": 400,
                    "last_transaction_date": "2026-06-01",
                }
            },
        ],
    )
    save_fact(session, candidate, fact_type="can_cancel", value=True)
    session.refresh(candidate)
    assert candidate.expected_gross_low == 400


def test_switch_requires_replacement_observation_before_realized_value(session):
    candidate = _recurring_candidate(session, "monthly", 25, last_date="2025-01-01")
    save_fact(session, candidate, fact_type="can_cancel", value=True)
    save_fact(session, candidate, fact_type="new_recurring_amount", value=10)
    candidate.lifecycle_status = "accepted"
    session.add(candidate)
    session.commit()
    assert observe_recurring_outcomes(session) == 0
    assert session.exec(select(OpportunityOutcomeObservation)).all() == []


def test_observer_requires_durable_owner_action_timestamp(session):
    candidate = _recurring_candidate(session, "monthly", 25, last_date="2025-01-01")
    candidate.lifecycle_status = "accepted"
    session.add(candidate)
    session.commit()

    assert observe_recurring_outcomes(session) == 0
    assert session.exec(select(OpportunityOutcomeObservation)).all() == []


def test_random_repeats_and_required_obligation_are_suppressed(session):
    _tx(session, "Random shop", 30, [date(2026, 1, 1), date(2026, 2, 17), date(2026, 4, 9)])
    session.add(Bill(name="Electric", amount=100, required=True))
    session.commit()
    _tx(session, "Electric", 100, [date(2026, 1, 5), date(2026, 2, 5), date(2026, 3, 5)])
    assert all(row["merchant_key"] != "random shop" for row in detect_recurring_subscriptions(session))
    assert all(row["merchant_key"] != "electric" for row in detect_recurring_subscriptions(session))


def test_confirmation_is_required_then_zero_cost_cancel_can_rank(session, monkeypatch):
    _tx(session, "Video service", 25, [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)])
    finding = _subscription_opportunities(session)[0]
    candidate = normalize_v1_opportunity(session, finding)
    assert candidate.expected_net_low is None
    monkeypatch.setattr(
        "app.core.v2_opportunities.calculate_cashflow",
        lambda _s: {"money_state": {"deployable_surplus": 0, "experimental_capital": 0, "risk_windows": []}},
    )
    respond_to_recurring_candidate(session, candidate, "cancel")
    session.refresh(candidate)
    assert candidate.expected_net_low == 300
    assert candidate.required_capital == 0


def test_owner_keep_suppresses_repeated_scan(session):
    _tx(session, "Video service", 25, [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)])
    finding = _subscription_opportunities(session)[0]
    candidate = normalize_v1_opportunity(session, finding)
    respond_to_recurring_candidate(session, candidate, "keep")
    sync_household_recurring_cost_moves(session)
    session.refresh(candidate)
    assert candidate.lifecycle_status == "dismissed" and candidate.source_label == "owner_kept"
    assert (
        len(session.exec(select(OpportunityCandidate).where(OpportunityCandidate.category == "subscription")).all())
        == 1
    )


def test_live_portfolio_safety_removes_capital_candidate(session, monkeypatch):
    candidate = OpportunityCandidate(
        identity_key="external-capital",
        title="Capital action",
        category="other",
        source_type="owner_supplied",
        expected_net_low=500,
        expected_net_high=500,
        required_capital=400,
        capital_at_risk=0,
        qualification_status="qualified",
        lifecycle_status="qualified",
        safety_eligible=True,
    )
    session.add(candidate)
    session.commit()
    monkeypatch.setattr(
        "app.core.v2_opportunities.calculate_cashflow",
        lambda _s: {"money_state": {"deployable_surplus": 0, "experimental_capital": 0, "risk_windows": []}},
    )
    assert not any(row["id"] == candidate.id for row in ranked_candidates(session))


def test_no_duplicate_candidate_on_repeated_transaction_refresh(session):
    _tx(session, "Video service", 25, [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)])
    sync_household_recurring_cost_moves(session)
    sync_household_recurring_cost_moves(session)
    rows = session.exec(select(OpportunityCandidate).where(OpportunityCandidate.category == "subscription")).all()
    assert len(rows) == 1


def test_recurring_outcome_waits_for_two_post_action_intervals(session):
    today = date.today()
    candidate = _recurring_candidate(
        session,
        "monthly",
        25,
        last_date=today.fromordinal(today.toordinal() - 100).isoformat(),
    )
    candidate.lifecycle_status = "accepted"
    session.add(candidate)
    session.commit()
    action = _action(session, candidate, 70)
    assert observe_recurring_outcomes(session) == 1
    assert observe_recurring_outcomes(session) == 0
    outcome = session.exec(
        select(OpportunityOutcomeObservation).where(OpportunityOutcomeObservation.candidate_id == candidate.id)
    ).one()
    assert outcome.outcome_status == "in_progress"
    assert outcome.actual_gross_value == 50
    assert outcome.actual_net_value is None
    assert candidate.lifecycle_status == "accepted"
    assert realized_value_summary(session)["observed_total_realized_value"] == 0

    # Historical gaps before an action are never credited as savings.
    action.created_at = datetime.now(UTC).replace(tzinfo=None)
    candidate.evidence_json = json.dumps(
        [
            {
                "math": {
                    "merchant_key": "service",
                    "cadence": "monthly",
                    "avg_amount": 25,
                    "last_transaction_date": today.fromordinal(today.toordinal() - 400).isoformat(),
                    "cadence_days": 30,
                }
            }
        ]
    )
    session.add_all([action, candidate])
    session.commit()
    assert observe_recurring_outcomes(session) == 0
    session.refresh(outcome)
    assert outcome.actual_gross_value == 50
    assert outcome.actual_net_value is None
    assert outcome.outcome_status == "in_progress"

    # A full post-action horizon permits terminal completion.
    action.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=400)
    candidate.evidence_json = json.dumps(
        [
            {
                "math": {
                    "merchant_key": "service",
                    "cadence": "monthly",
                    "avg_amount": 25,
                    "last_transaction_date": today.fromordinal(today.toordinal() - 800).isoformat(),
                    "cadence_days": 30,
                }
            }
        ]
    )
    session.add_all([action, candidate])
    session.commit()
    save_fact(session, candidate, fact_type="actual_cancellation_fee", value=0)
    assert observe_recurring_outcomes(session) == 1
    session.refresh(outcome)
    session.refresh(candidate)
    assert outcome.outcome_status == "completed"
    assert outcome.actual_gross_value == 300
    assert outcome.actual_net_value == 300
    assert candidate.lifecycle_status == "outcome_recorded"
    assert realized_value_summary(session)["observed_total_realized_value"] == 300


@pytest.mark.parametrize(
    ("cadence", "amount", "elapsed_days", "expected_gross"),
    [
        ("weekly", 25, 21, 75),
        ("annual", 120, 800, 120),
    ],
)
def test_observed_value_uses_elapsed_cadence_not_annual_projection(
    session, cadence, amount, elapsed_days, expected_gross
):
    baseline_days = elapsed_days + 400 if cadence == "annual" else elapsed_days
    last_date = date.today().fromordinal(date.today().toordinal() - baseline_days)
    candidate = _recurring_candidate(
        session,
        cadence,
        amount,
        last_date=last_date.isoformat(),
    )
    save_fact(session, candidate, fact_type="can_cancel", value=True)
    candidate.lifecycle_status = "accepted"
    session.add(candidate)
    session.commit()
    save_fact(session, candidate, fact_type="actual_cancellation_fee", value=0)
    _action(session, candidate, elapsed_days)

    assert observe_recurring_outcomes(session) == 1
    outcome = session.exec(
        select(OpportunityOutcomeObservation).where(OpportunityOutcomeObservation.candidate_id == candidate.id)
    ).one()
    assert outcome.actual_gross_value == expected_gross
    if cadence == "weekly":
        assert outcome.actual_gross_value != candidate.expected_net_low
    if cadence == "annual":
        assert outcome.outcome_status == "completed"
        assert realized_value_summary(session)["observed_total_realized_value"] == expected_gross


def test_observed_value_deducts_known_cancellation_fee_once(session):
    last_date = date.today().fromordinal(date.today().toordinal() - 70)
    candidate = _recurring_candidate(session, "monthly", 25, last_date=last_date.isoformat())
    save_fact(session, candidate, fact_type="can_cancel", value=True)
    save_fact(session, candidate, fact_type="actual_cancellation_fee", value=10)
    candidate.lifecycle_status = "accepted"
    session.add(candidate)
    session.commit()
    _action(session, candidate, 70)

    assert observe_recurring_outcomes(session) == 1
    outcome = session.exec(
        select(OpportunityOutcomeObservation).where(OpportunityOutcomeObservation.candidate_id == candidate.id)
    ).one()
    assert outcome.actual_gross_value == 50
    assert outcome.actual_direct_cost == 10
    assert outcome.actual_net_value == 40


def test_projected_fee_is_not_claimed_as_actual_cost(session):
    last_date = date.today().fromordinal(date.today().toordinal() - 70)
    candidate = _recurring_candidate(session, "monthly", 25, last_date=last_date.isoformat())
    save_fact(session, candidate, fact_type="can_cancel", value=True)
    save_fact(session, candidate, fact_type="cancellation_fee", value=100)
    candidate.lifecycle_status = "accepted"
    session.add(candidate)
    session.commit()
    _action(session, candidate, 70)

    assert observe_recurring_outcomes(session) == 1
    outcome = session.exec(
        select(OpportunityOutcomeObservation).where(OpportunityOutcomeObservation.candidate_id == candidate.id)
    ).one()
    assert outcome.actual_direct_cost is None
    assert outcome.actual_net_value is None


def test_terminal_horizon_with_unknown_actual_fee_stays_nonterminal(session):
    today = date.today()
    candidate = _recurring_candidate(
        session,
        "monthly",
        25,
        last_date=today.fromordinal(today.toordinal() - 800).isoformat(),
    )
    save_fact(session, candidate, fact_type="can_cancel", value=True)
    candidate.lifecycle_status = "accepted"
    session.add(candidate)
    session.commit()
    _action(session, candidate, 400)

    assert observe_recurring_outcomes(session) == 1
    outcome = session.exec(
        select(OpportunityOutcomeObservation).where(OpportunityOutcomeObservation.candidate_id == candidate.id)
    ).one()
    assert outcome.outcome_status == "in_progress"
    assert outcome.actual_gross_value == 300
    assert outcome.actual_direct_cost is None
    assert outcome.actual_net_value is None
    assert realized_value_summary(session)["observed_total_realized_value"] == 0


def test_explicit_zero_actual_fee_is_distinct_from_unknown(session):
    last_date = date.today().fromordinal(date.today().toordinal() - 70)
    candidate = _recurring_candidate(session, "monthly", 25, last_date=last_date.isoformat())
    save_fact(session, candidate, fact_type="can_cancel", value=True)
    save_fact(session, candidate, fact_type="actual_cancellation_fee", value=0)
    candidate.lifecycle_status = "accepted"
    session.add(candidate)
    session.commit()
    _action(session, candidate, 70)

    assert observe_recurring_outcomes(session) == 1
    outcome = session.exec(
        select(OpportunityOutcomeObservation).where(OpportunityOutcomeObservation.candidate_id == candidate.id)
    ).one()
    assert outcome.actual_gross_value == 50
    assert outcome.actual_direct_cost == 0
    assert outcome.actual_net_value == 50


def test_observed_value_preserves_negative_net_when_actual_fee_exceeds_savings(session):
    last_date = date.today().fromordinal(date.today().toordinal() - 70)
    candidate = _recurring_candidate(session, "monthly", 25, last_date=last_date.isoformat())
    save_fact(session, candidate, fact_type="can_cancel", value=True)
    save_fact(session, candidate, fact_type="actual_cancellation_fee", value=100)
    candidate.lifecycle_status = "accepted"
    session.add(candidate)
    session.commit()
    _action(session, candidate, 70)

    assert observe_recurring_outcomes(session) == 1
    outcome = session.exec(
        select(OpportunityOutcomeObservation).where(OpportunityOutcomeObservation.candidate_id == candidate.id)
    ).one()
    assert outcome.actual_gross_value == 50
    assert outcome.actual_direct_cost == 100
    assert outcome.actual_net_value == -50


def test_signed_negative_terminal_value_reduces_realized_summary(session):
    today = date.today()
    candidate = _recurring_candidate(
        session,
        "monthly",
        25,
        last_date=today.fromordinal(today.toordinal() - 800).isoformat(),
    )
    save_fact(session, candidate, fact_type="can_cancel", value=True)
    save_fact(session, candidate, fact_type="actual_cancellation_fee", value=400)
    candidate.lifecycle_status = "accepted"
    session.add(candidate)
    session.commit()
    _action(session, candidate, 400)

    assert observe_recurring_outcomes(session) == 1
    outcome = session.exec(
        select(OpportunityOutcomeObservation).where(OpportunityOutcomeObservation.candidate_id == candidate.id)
    ).one()
    assert outcome.outcome_status == "completed"
    assert outcome.actual_gross_value == 300
    assert outcome.actual_direct_cost == 400
    assert outcome.actual_net_value == -100
    assert realized_value_summary(session)["observed_total_realized_value"] == -100
