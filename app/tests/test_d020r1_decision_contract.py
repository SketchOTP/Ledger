"""D020R1 guards for owner context and decision briefs."""

import json

from app.core.opportunity_research import build_research_brief
from app.core.owner_context import build_owner_context, build_provider_owner_context
from app.core.v2_opportunities import (
    add_asset,
    add_skill,
    build_candidate_decision_brief,
    ingest_candidate,
    profile_payload,
    update_profile,
)


def test_capability_completeness_distinguishes_defensive_and_growth_readiness(session):
    empty = profile_payload(session)
    assert empty["completeness"]["defensive"] == "DEFENSIVE_READY"
    assert empty["completeness"]["growth"] == "GROWTH_LIMITED"
    add_skill(session, {"name": "Python automation", "monetization_willingness": True})
    ready = profile_payload(session)
    assert ready["completeness"]["status"] == "GROWTH_READY"


def test_full_profile_reaches_bounded_provider_context_without_private_text(session):
    update_profile(
        session,
        {
            "max_setup_hours": 4,
            "max_recurring_hours_per_week": 3,
            "preferred_effort_level": "low",
            "customer_interaction_willingness": False,
            "sales_outreach_willingness": True,
            "physical_labor_willingness": False,
            "prefers_async": True,
            "prefers_automated": True,
            "prefers_low_touch": True,
            "prefers_recurring_income": True,
            "prefers_scalable_income": True,
            "allows_one_time_income": False,
            "desired_time_to_value_days": 30,
            "minimum_meaningful_payoff": 500,
            "max_preferred_capital": 250,
            "refused_activities": ["private refusal"],
            "notes": "PRIVATE OWNER NOTE and account 123456",
        },
    )
    add_skill(session, {"name": "Python automation", "category": "software", "monetization_willingness": True})
    add_asset(
        session,
        {
            "name": "Template library",
            "asset_type": "software",
            "availability": "available",
            "monetization_willingness": True,
        },
    )
    context = build_owner_context(session, user_id="test-user")
    provider = build_provider_owner_context(context)
    encoded = json.dumps(provider)
    assert provider["capability"]["preferred_effort_level"] == "low"
    assert provider["capability"]["desired_time_to_value_days"] == 30
    assert provider["capability"]["max_preferred_capital_band"] == "250_to_999"
    assert "PRIVATE OWNER NOTE" not in encoded
    assert "private refusal" not in encoded
    assert "123456" not in encoded
    brief = build_research_brief(session, {"money_state": {"deployable_surplus": 900, "experimental_capital": 100}})
    assert brief["capability"]["sales_outreach_willingness"] is True
    assert brief["capability"]["allows_one_time_income"] is False
    assert brief["personalization_limited"] is False


def test_decision_brief_keeps_unknowns_and_supplies_specific_next_step(session):
    candidate = ingest_candidate(
        session,
        {
            "title": "Review recurring charge",
            "category": "subscription",
            "source_type": "deterministic_internal",
            "required_capital": 0,
        },
    )
    brief = build_candidate_decision_brief(candidate)
    assert brief["quality_state"] == "NEEDS_ECONOMICS"
    assert brief["expected_net_low"] is None
    assert "probability_range" not in brief["missing_facts"]
    assert brief["next_step"] == "Confirm whether the recurring charge can be canceled or downgraded."
    assert brief["why_now"] == "Validation is required before this candidate can be ranked safely."


def test_active_candidate_remains_actionable_and_brief_points_to_outcome(session):
    candidate = ingest_candidate(
        session,
        {
            "title": "Accepted move",
            "source_type": "owner_supplied",
            "expected_net_low": 100,
            "expected_net_high": 200,
            "probability_low": 0.5,
            "probability_high": 0.8,
        },
    )
    candidate.lifecycle_status = "accepted"
    session.add(candidate)
    session.commit()
    brief = build_candidate_decision_brief(candidate)
    assert brief["quality_state"] == "ACTIONABLE"
    assert brief["next_step"] == "Complete the next owner action and record the observed outcome."
