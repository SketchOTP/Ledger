import json

from sqlmodel import select

from app.core.v2_opportunities import (
    add_asset,
    add_skill,
    create_candidate_action,
    get_profile,
    ingest_candidate,
    normalize_v1_opportunities,
    normalize_v1_opportunity,
    qualify_candidate,
    rank_candidate,
    ranked_candidates,
    top_moves,
    transition_candidate,
    update_profile,
)
from app.db.models import BorrowerProfile, OpportunityTransition


def _candidate(session, **overrides):
    data = {
        "title": "Reusable tool",
        "source_type": "owner_supplied",
        "expected_net_low": 300,
        "expected_net_high": 500,
        "probability_low": 0.5,
        "probability_high": 0.8,
        "setup_hours": 1,
        "recurring_hours_per_week": 0,
        "scalability": 0.8,
        "repeatability": 0.8,
        "automation_potential": 0.8,
        "reversibility": 0.8,
    }
    data.update(overrides)
    return ingest_candidate(session, data)


def _money_state(deployable=1000, experimental=250, risk_windows=None):
    return {
        "money_state": {
            "deployable_surplus": deployable,
            "experimental_capital": experimental,
            "risk_windows": risk_windows or [],
        }
    }


def test_profile_accepts_owner_defined_skills_assets_and_limits(session):
    update_profile(session, {"max_setup_hours": 3, "refused_activities": ["cold outreach"]})
    assert add_skill(session, {"name": "custom CAD work", "category": "other"}).name == "custom CAD work"
    assert add_asset(session, {"name": "my niche tool", "asset_type": "other"}).name == "my niche tool"
    assert get_profile(session).max_setup_hours == 3


def test_capital_and_zero_capital_qualification(session):
    session.add(BorrowerProfile(checking_balance=10000, target_buffer=100))
    session.commit()
    blocked = _candidate(session, required_capital=9000, capital_at_risk=9000)
    assert not qualify_candidate(session, blocked)["eligible"]
    free = _candidate(session, title="Free reusable guide", required_capital=0, capital_at_risk=0)
    assert qualify_candidate(session, free)["eligible"]


def test_qualification_enforces_money_evidence_and_owner_constraints(session):
    update_profile(
        session,
        {
            "max_setup_hours": 2,
            "max_recurring_hours_per_week": 1,
            "customer_interaction_willingness": False,
            "refused_activities": ["cold outreach"],
        },
    )
    protected = _candidate(session, title="Consumes protected cash", required_capital=1001)
    assert (
        "required_capital_exceeds_deployable_surplus"
        in qualify_candidate(session, protected, _money_state())["reasons"]
    )
    speculative = _candidate(session, title="Risky experiment", required_capital=100, capital_at_risk=251)
    assert (
        "capital_at_risk_exceeds_experimental_capital"
        in qualify_candidate(session, speculative, _money_state())["reasons"]
    )
    safe_capital = _candidate(session, title="Non speculative purchase", required_capital=500, capital_at_risk=0)
    assert qualify_candidate(session, safe_capital, _money_state(experimental=0))["eligible"]
    locked = _candidate(session, title="Locked savings", liquidity_lockup_days=90)
    assert (
        "liquidity_lockup_conflicts_with_risk_window"
        in qualify_candidate(session, locked, _money_state(risk_windows=[{"date": "2030-01-01"}]))["reasons"]
    )
    refused = _candidate(
        session, title="Cold calls", setup_hours=3, required_activities_json='["customer_interaction", "cold outreach"]'
    )
    reasons = qualify_candidate(session, refused, _money_state())["reasons"]
    assert {"setup_hours_exceed_owner_limit", "customer_interaction_refused", "owner_refused_activity"} <= set(reasons)
    stale = _candidate(
        session,
        title="Old public offer",
        source_type="researched_external",
        freshness_status="stale",
        verification_status="verified",
    )
    assert "external_evidence_not_verified_fresh" in qualify_candidate(session, stale, _money_state())["reasons"]
    ai = _candidate(session, title="AI conjecture", source_type="ai_hypothesis")
    assert "ai_hypothesis_requires_independent_evidence" in qualify_candidate(session, ai, _money_state())["reasons"]
    unknown = _candidate(session, title="No economics", expected_net_low=None, expected_net_high=None)
    assert "economics_unknown" in qualify_candidate(session, unknown, _money_state())["reasons"]


def test_validation_experiment_is_bounded_by_experimental_capital_and_owner_hours(session):
    update_profile(session, {"max_setup_hours": 2})
    candidate = _candidate(
        session,
        title="Bounded listing experiment",
        validation_experiment={
            "hypothesis": "one listing tests demand",
            "max_capital": 251,
            "max_hours": 3,
            "success_metric": "one qualified inquiry",
            "failure_metric": "no inquiry",
            "review_date": "2030-01-01",
            "stop_loss": "stop at limits",
            "next_decision": "review evidence",
        },
    )
    result = qualify_candidate(session, candidate, _money_state())
    assert "experiment_max_capital_exceeds_experimental_capital" in result["reasons"]
    assert "experiment_max_hours_exceed_owner_limit" in result["reasons"]


def test_lifecycle_rejects_nonsensical_terminal_transition(session):
    candidate = _candidate(session)
    transition_candidate(session, candidate, "researched")
    transition_candidate(session, candidate, "rejected")
    try:
        transition_candidate(session, candidate, "accepted")
    except ValueError:
        pass
    else:
        raise AssertionError("terminal candidate became accepted")


def test_lifecycle_path_is_audited_and_forbidden_shortcuts_fail(session):
    candidate = _candidate(session, title="Lifecycle candidate")
    for target in ("unverified", "researched", "qualified", "proposed", "accepted", "attempted", "outcome_recorded"):
        transition_candidate(session, candidate, target, "test path")
    transitions = session.exec(
        select(OpportunityTransition).where(OpportunityTransition.candidate_id == candidate.id)
    ).all()
    assert [row.to_status for row in transitions] == [
        "discovered",
        "unverified",
        "researched",
        "qualified",
        "proposed",
        "accepted",
        "attempted",
        "outcome_recorded",
    ]
    for bad in ("accepted", "attempted"):
        try:
            transition_candidate(session, candidate, bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"terminal candidate accepted {bad}")
    shortcut = _candidate(session, title="Shortcut")
    for target in ("outcome_recorded", "accepted"):
        try:
            transition_candidate(session, shortcut, target)
        except ValueError:
            pass
        else:
            raise AssertionError(f"discovered candidate accepted {target}")


def test_ranking_penalizes_linear_labor_and_action_handoff(session):
    session.add(BorrowerProfile(checking_balance=10000, target_buffer=100))
    session.commit()
    good = _candidate(session, title="Reusable asset", expected_net_low=300, expected_net_high=500)
    labor = _candidate(
        session,
        title="Hourly service",
        expected_net_low=800,
        expected_net_high=900,
        setup_hours=20,
        recurring_hours_per_week=8,
        continued_labor_dependence=1,
        scalability=0,
        automation_potential=0,
    )
    assert rank_candidate(good)["score"] > rank_candidate(labor)["score"]
    transition_candidate(session, good, "researched")
    assert qualify_candidate(session, good)["eligible"]
    transition_candidate(session, good, "proposed")
    transition_candidate(session, good, "accepted")
    first = create_candidate_action(session, good)
    assert first.opportunity_candidate_id == good.id
    assert good.lifecycle_status == "attempted"
    assert create_candidate_action(session, good).id == first.id


def test_deduplication_preserves_source_evidence_without_false_merges(session):
    owner = _candidate(
        session,
        title="Sell reusable template",
        expected_net_low=400,
        expected_net_high=600,
        evidence=[{"owner": "idea"}],
    )
    same = _candidate(
        session,
        title="Sell reusable template",
        expected_net_low=400,
        expected_net_high=600,
        evidence=[{"owner": "idea"}],
    )
    assert same.id == owner.id
    researched = _candidate(
        session,
        title="Sell reusable template",
        source_type="researched_external",
        source_url="https://example.test/offer",
        expected_net_low=400,
        expected_net_high=600,
        verification_status="verified",
        evidence=[{"research": "source"}],
    )
    assert researched.id == owner.id
    assert owner.verification_status == "verified"
    assert {"owner", "research"} <= {next(iter(item)) for item in json.loads(owner.evidence_json) if item}
    distinct = _candidate(session, title="Sell reusable template", expected_net_low=4000, expected_net_high=6000)
    assert distinct.id != owner.id


def test_v1_normalization_enters_the_same_candidate_contract(session):
    row = normalize_v1_opportunity(
        session,
        {
            "title": "High APR card",
            "category": "credit_card",
            "why": "APR is expensive",
            "estimated_yearly_savings": 240,
            "confidence": 0.8,
            "requires_manual_verification": True,
            "sources": ["https://example.test/card"],
            "math": {"apr": 29},
        },
    )
    assert row.source_type == "deterministic_internal"
    assert row.verification_status == "unverified"
    assert json.loads(row.evidence_json)[0]["v1_source"] == "opportunity_engine"
    batch = normalize_v1_opportunities(
        session,
        [
            {
                "title": "Subscription leakage",
                "category": "subscriptions",
                "estimated_monthly_savings": 15,
                "confidence": 0.7,
                "requires_manual_verification": False,
            }
        ],
    )
    assert batch[0].source_label == "opportunity_engine"


def test_rank_components_and_top_moves_are_bounded(session):
    base = _candidate(
        session,
        title="Base",
        expected_net_low=1000,
        expected_net_high=1000,
        probability_low=0.8,
        probability_high=0.8,
        capability_fit_score=0.7,
        verification_status="verified",
        freshness_status="current",
    )
    more_value = _candidate(
        session,
        title="More value",
        expected_net_low=2000,
        expected_net_high=2000,
        probability_low=0.8,
        probability_high=0.8,
        capability_fit_score=0.7,
        verification_status="verified",
        freshness_status="current",
    )
    costly = _candidate(
        session,
        title="Costly",
        expected_net_low=1000,
        expected_net_high=1000,
        probability_low=0.8,
        probability_high=0.8,
        setup_hours=30,
        recurring_hours_per_week=10,
        capital_at_risk=900,
        liquidity_lockup_days=90,
        complexity=0.8,
        downside_severity=0.8,
        continued_labor_dependence=0.8,
        capability_fit_score=0.1,
    )
    assert rank_candidate(more_value)["score"] > rank_candidate(base)["score"] > rank_candidate(costly)["score"]
    for row in (base, more_value, costly):
        row.qualification_status = "qualified"
        row.safety_eligible = True
        session.add(row)
    session.commit()
    assert len(top_moves(session)) <= 3
    costly.safety_eligible = False
    session.add(costly)
    session.commit()
    assert costly.id not in {row["id"] for row in ranked_candidates(session)}


def test_top_moves_can_recommend_nothing_or_only_the_best_three(session):
    for index in range(3):
        low_value = _candidate(
            session,
            title=f"Marginal {index}",
            expected_net_low=10,
            expected_net_high=10,
            probability_low=0.1,
            probability_high=0.1,
            setup_hours=20,
            recurring_hours_per_week=10,
        )
        low_value.qualification_status = "qualified"
        low_value.safety_eligible = True
        session.add(low_value)
    session.commit()
    assert top_moves(session) == []

    for index in range(5):
        worthwhile = _candidate(
            session,
            title=f"Worthwhile {index}",
            expected_net_low=1000 + index * 100,
            expected_net_high=1000 + index * 100,
            probability_low=0.8,
            probability_high=0.8,
            capability_fit_score=0.8,
            scalability=0.8,
            repeatability=0.8,
            automation_potential=0.8,
            reversibility=0.8,
            verification_status="verified",
            freshness_status="current",
        )
        worthwhile.qualification_status = "qualified"
        worthwhile.safety_eligible = True
        session.add(worthwhile)
    session.commit()
    assert len(top_moves(session)) == 3
