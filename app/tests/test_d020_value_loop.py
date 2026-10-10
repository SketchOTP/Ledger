"""D020 guards for decision-pool hygiene, evidence-to-economics, and value accounting."""

from app.core.financial_snapshot import FinancialStateSnapshot
from app.core.opportunity_engine import _credit_card_opportunities, _subscription_opportunities
from app.core.opportunity_research import research_facts_for_snapshot
from app.core.v2_opportunities import (
    candidate_history,
    candidate_quality,
    decision_pool,
    opportunity_quality_summary,
    realized_value_summary,
)
from app.db.models import (
    CapabilitySkill,
    CreditCard,
    OpportunityCandidate,
    OpportunityEvidence,
    OpportunityResearchRun,
    Transaction,
)


def _candidate(session, **kwargs):
    row = OpportunityCandidate(
        identity_key=kwargs.pop("identity_key", "d020-" + str(len(session.new))), title="fixture", **kwargs
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def test_archival_candidates_are_out_of_current_decision_pool(session):
    archival = _candidate(session, category="financial_offer", source_type="researched_external")
    session.add(CapabilitySkill(name="Explicit fixture skill"))
    session.commit()
    current = _candidate(session, category="capability_matched_growth", source_type="owner_supplied")
    assert candidate_quality(archival) == "ARCHIVAL"
    assert archival not in decision_pool(session)
    assert current in decision_pool(session)
    assert archival in candidate_history(session)


def test_unknown_economics_are_needs_economics_and_telemetry_is_privacy_safe(session):
    session.add(CapabilitySkill(name="Explicit fixture skill"))
    session.commit()
    _candidate(session, category="capability_matched_growth", source_type="owner_supplied")
    summary = opportunity_quality_summary(session)
    assert summary["counts"]["NEEDS_ECONOMICS"] == 1
    assert summary["current_decision_inventory"] == 1
    assert "title" not in summary


def test_d003_rate_claims_become_snapshot_facts(session):
    candidate = _candidate(
        session,
        identity_key="d020-mortgage",
        category="mortgage_refinance",
        source_type="researched_external",
        verification_status="verified",
        freshness_status="current",
    )
    run = OpportunityResearchRun(status="success")
    session.add(run)
    session.commit()
    session.add(OpportunityEvidence(
        research_run_id=run.id,
        candidate_id=candidate.id,
        provider_id="fred_mortgage_opportunity",
        source_url="https://fred.stlouisfed.org/series/MORTGAGE30US",
        claim_type="rate",
        factual_value_json="6.25",
        evidence_strength=0.9,
    ))
    session.commit()
    facts = research_facts_for_snapshot(session)
    assert facts == [{
        "product_type": "mortgage_refinance",
        "estimated_apr": 6.25,
        "source_url": "https://fred.stlouisfed.org/series/MORTGAGE30US",
        "provider": "fred_mortgage_opportunity",
        "confidence": 0.9,
        "verified": True,
    }]


def test_unsupported_card_and_subscription_savings_remain_unknown(session):
    session.add(
        CreditCard(
            issuer="Fixture", card_name="Fixture card", balance=1000, credit_limit=5000, apr=24, minimum_payment=40
        )
    )
    session.commit()
    snapshot = FinancialStateSnapshot(
        raw_credit_cards=[{"issuer": "Fixture", "balance": 1000, "apr": 24}],
        credit=[{"balance_transfer_candidate": True}],
    )
    card = _credit_card_opportunities(snapshot)[0]
    assert card["estimated_yearly_savings"] == 0
    assert card["warnings"] == []
    from datetime import date
    session.add_all([
        Transaction(description="streaming", amount=20, category="entertainment", transaction_date=date(2026, 1, 1)),
        Transaction(description="streaming", amount=20, category="entertainment", transaction_date=date(2026, 2, 1)),
    ])
    session.commit()
    subscription = _subscription_opportunities(session)[0]
    assert subscription["estimated_yearly_savings"] == 0
    assert subscription["math"]["estimated_annual_cost"] == 240


def test_realized_value_excludes_projected_values(session):
    completed = _candidate(
        session,
        identity_key="d020-completed",
        expected_net_low=900,
        expected_net_high=1000,
        lifecycle_status="outcome_recorded",
    )
    pending = _candidate(session, identity_key="d020-pending", expected_net_low=500, expected_net_high=500)
    from app.db.models import OpportunityOutcomeObservation
    session.add(
        OpportunityOutcomeObservation(candidate_id=completed.id, outcome_status="completed", actual_net_value=125)
    )
    session.commit()
    summary = realized_value_summary(session)
    assert summary["completed_outcomes"] == 1
    assert summary["observed_total_realized_value"] == 125
    assert summary["projected_values_excluded"] is True
    assert pending.expected_net_low == 500
