"""Canonical V2 intelligence refresh.

The scheduled opportunity path belongs here.  Legacy recommendation/report
generation remains available to manual compatibility routes, but it is not a
prerequisite for V2 candidates, qualification, or ranking.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.core.cashflow_engine import calculate_cashflow
from app.core.financial_snapshot import build_financial_snapshot
from app.core.household_advisor import build_advisory, collect_hypothesis_evidence, persist_advisory
from app.core.opportunity_engine import detect_opportunities
from app.core.opportunity_research import (
    mark_stale_evidence,
    research_facts_for_snapshot,
    research_hypothesis_claims,
    run_opportunity_research,
)
from app.core.v2_opportunities import (
    normalize_v1_opportunities,
    qualify_candidate,
    top_moves,
)
from app.db.models import OpportunityCandidate


def run_v2_intelligence_refresh(
    session: Session,
    *,
    trigger: str = "manual",
    scope: str = "all",
) -> dict[str, Any]:
    """Run one canonical, V2-native opportunity refresh.

    The sequence intentionally stops at ``OpportunityCandidate``.  It does
    not call the legacy refresh cycle, persist ``Recommendation`` rows, or
    create recommendation-linked action-plan rows.  Existing legacy history
    remains readable through compatibility routes and backup/restore.
    """

    stale_candidates = mark_stale_evidence(session)
    cashflow = calculate_cashflow(session)

    # Originate a bounded household-specific hypothesis set before research so
    # the following evidence pass can target the actual ideas rather than only
    # broad capability themes.
    provisional_advisory = build_advisory(session, generate_hypotheses=True)

    # D003 is the sole bounded external-research orchestration path here.  It
    # already persists provenance, normalizes discoveries into candidates, and
    # applies D002 qualification to each external result.
    research = run_opportunity_research(
        session,
        scope=scope,
        trigger=trigger,
        cashflow=cashflow,
    )

    # The deterministic detector is presentation-neutral and consumes the
    # canonical financial snapshot.  Normalize its findings through the same
    # candidate contract as external and owner-supplied opportunities.
    # Feed structured D003 claims into the same snapshot used by the
    # deterministic detector.  This closes the old research-after-snapshot
    # disconnect without interpreting provider narrative as economics.
    snapshot = build_financial_snapshot(session, research_offers=research_facts_for_snapshot(session))
    defensive_findings = detect_opportunities(snapshot, session)
    defensive_candidates = normalize_v1_opportunities(session, defensive_findings)

    qualified = 0
    rejected = 0
    for candidate in defensive_candidates:
        result = qualify_candidate(session, candidate, cashflow)
        if result["eligible"]:
            qualified += 1
        else:
            rejected += 1

    # Household-specific hypotheses are bounded, unverified inputs to the same
    # candidate contract. They never author economics or bypass D002.
    hypotheses = provisional_advisory.get("hypotheses", [])
    # Acquire claim-specific evidence first, then reconcile each candidate's
    # persisted evidence state from that same snapshot before qualification.
    hypothesis_research = research_hypothesis_claims(session, hypotheses, cashflow)
    evidence_stage = collect_hypothesis_evidence(session, hypotheses)
    evidence_stage["hypothesis_research"] = hypothesis_research
    for hypothesis in hypotheses:
        candidate = (
            session.get(OpportunityCandidate, hypothesis.get("candidate_id"))
            if hypothesis.get("candidate_id")
            else None
        )
        if candidate is not None:
            qualify_candidate(session, candidate, cashflow)
    # Rebuild from the same hypothesis rows only after evidence and D002
    # qualification. This prevents first-refresh allocation from using stale
    # eligibility or regenerating a different candidate set.
    advisory = build_advisory(session, generate_hypotheses=False, hypotheses_override=hypotheses)
    persist_advisory(session, advisory)

    ranked = top_moves(session)
    return {
        "trigger": trigger,
        "scope": scope,
        "snapshot_id": snapshot.snapshot_id,
        "stale_candidates": stale_candidates,
        "research": research,
        "defensive_findings": len(defensive_findings),
        "defensive_candidates": len(defensive_candidates),
        "defensive_qualified": qualified,
        "defensive_rejected": rejected,
        "ranked_count": len(ranked),
        "hypotheses_generated": len(advisory.get("hypotheses", [])),
        "hypothesis_evidence": evidence_stage,
        "top_moves": ranked,
        "legacy_refresh_calls": 0,
        "legacy_recommendation_writes": 0,
    }
