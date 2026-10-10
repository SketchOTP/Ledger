"""V2 capability, candidate, qualification, and ranking contracts.

This module is deterministic: it stores hypotheses without upgrading them into
facts, and treats D001 money-state safety as a hard prerequisite to ranking.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from app.core.cashflow_engine import calculate_cashflow
from app.db.models import (
    CapabilityAsset,
    CapabilityProfile,
    CapabilitySkill,
    CreditCard,
    Debt,
    FinancialAction,
    FinancialOffer,
    LiquidAccount,
    MortgageAccount,
    OpportunityCandidate,
    OpportunityEvidence,
    OpportunityOutcomeObservation,
    OpportunityTransition,
    OpportunityValidationFact,
    Transaction,
)

LIFECYCLE = frozenset(
    {
        "discovered",
        "unverified",
        "researched",
        "qualified",
        "rejected",
        "proposed",
        "accepted",
        "dismissed",
        "attempted",
        "outcome_recorded",
    }
)
TERMINAL = frozenset({"rejected", "dismissed", "outcome_recorded"})
PENDING_QUALIFICATION_REASONS = frozenset(
    {
        "external_evidence_not_verified_fresh",
        "ai_hypothesis_requires_independent_evidence",
        "economics_unknown",
        "probability_unknown",
        "required_capital_unknown",
        "capital_at_risk_unknown",
        "setup_hours_unknown",
        "recurring_hours_unknown",
        "time_to_value_unknown",
        "comparison_apy_missing",
        "marketplace_fee_unknown",
        "conflicting_validation_facts",
    }
)
DETERMINISTIC_ECONOMIC_CATEGORIES = frozenset(
    {
        "cash_yield_benchmark",
        "savings",
        "mortgage_refinance",
        "credit_card",
        "debt_paydown",
        "recurring",
        "subscription",
    }
)
TRANSITIONS = {
    "discovered": {"unverified", "researched", "rejected"},
    "unverified": {"researched", "rejected"},
    "researched": {"qualified", "rejected"},
    "qualified": {"proposed", "rejected"},
    "proposed": {"accepted", "dismissed", "rejected"},
    "accepted": {"attempted", "dismissed"},
    "attempted": {"outcome_recorded", "dismissed"},
}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _loads(raw: str) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
        return value if isinstance(value, list) else []
    except json.JSONDecodeError:
        return []


def _loads_dict(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _table_payload(row: Any) -> dict[str, Any]:
    """SQLModel's model_dump omits ORM-loaded defaults; expose the durable row."""
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _identity(payload: dict[str, Any]) -> str:
    title = re.sub(r"[^a-z0-9]+", " ", str(payload.get("title") or "").lower()).strip()
    external_identity = str(payload.get("external_identity") or "").strip()
    canonical_identity = str(payload.get("canonical_identity") or "").strip()
    if canonical_identity and not payload.get("materially_distinct_terms"):
        raw = "|".join(("canonical", str(payload.get("category") or "other").lower(), canonical_identity.lower()))
        return hashlib.sha256(raw.encode()).hexdigest()[:32]
    if (
        payload.get("source_type") == "researched_external"
        and external_identity
        and not payload.get("materially_distinct_terms")
    ):
        # Repeated external observations of one named offer update its evidence
        # even when a non-material factual term changes. Providers must opt out
        # explicitly when changed terms define a new economic opportunity.
        raw = "|".join(
            (
                "external",
                str(payload.get("category") or "other").lower(),
                (canonical_identity or external_identity).lower(),
            )
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:32]
    # Source URLs identify evidence, not the underlying opportunity.  Economics
    # remain part of identity so two similarly named but materially different
    # offers cannot be collapsed merely because their wording matches.
    net = _median(payload.get("expected_net_low"), payload.get("expected_net_high"))
    capital = float(payload.get("required_capital") or 0)
    at_risk = float(payload.get("capital_at_risk") or 0)
    economics = "unknown" if net is None else f"{round(net, 2)}:{round(capital, 2)}:{round(at_risk, 2)}"
    raw = "|".join((str(payload.get("category") or "other").lower(), title, economics))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def get_profile(session: Session) -> CapabilityProfile:
    profile = session.get(CapabilityProfile, "default")
    if profile is None:
        profile = CapabilityProfile()
        session.add(profile)
        session.commit()
        session.refresh(profile)
    return profile


def profile_payload(session: Session) -> dict[str, Any]:
    profile = get_profile(session)
    skills = [_table_payload(row) for row in session.exec(select(CapabilitySkill)).all()]
    assets = [_table_payload(row) for row in session.exec(select(CapabilityAsset)).all()]
    return {
        "profile": _table_payload(profile),
        "skills": skills,
        "assets": assets,
        "refused_activities": _loads(profile.refused_activities_json),
        "completeness": profile_completeness(profile, skills, assets),
    }


def profile_completeness(
    profile: CapabilityProfile,
    skills: list[dict[str, Any]],
    assets: list[dict[str, Any]],
) -> dict[str, Any]:
    """Report readiness without inventing capability data.

    Defensive research never needs owner capabilities. Growth discovery does:
    only explicitly monetizable skills or available assets make it ready.
    """
    has_capability = any(
        row.get("monetization_willingness") and str(row.get("name") or "").strip() for row in skills
    ) or any(
        row.get("monetization_willingness")
        and row.get("availability") == "available"
        and str(row.get("name") or "").strip()
        for row in assets
    )
    growth = "GROWTH_READY" if has_capability else "GROWTH_LIMITED"
    return {
        "status": growth,
        "defensive": "DEFENSIVE_READY",
        "growth": growth,
        "has_explicit_capability": has_capability,
        "message": (
            "" if has_capability else "Growth discovery is limited until you add at least one skill or reusable asset."
        ),
    }


def update_profile(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    profile = get_profile(session)
    allowed = set(CapabilityProfile.model_fields) - {"id", "created_at", "updated_at", "refused_activities_json"}
    for key in allowed & payload.keys():
        setattr(profile, key, payload[key])
    if "refused_activities" in payload:
        profile.refused_activities_json = json.dumps(payload["refused_activities"] or [])
    profile.updated_at = _now()
    session.add(profile)
    session.commit()
    return profile_payload(session)


def add_skill(session: Session, payload: dict[str, Any]) -> CapabilitySkill:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("skill name is required")
    row = CapabilitySkill(
        name=name,
        **{k: v for k, v in payload.items() if k in {"category", "proficiency", "monetization_willingness", "notes"}},
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def update_skill(session: Session, skill_id: str, payload: dict[str, Any]) -> CapabilitySkill:
    row = session.get(CapabilitySkill, skill_id)
    if row is None:
        raise ValueError("skill not found")
    for key in {"name", "category", "proficiency", "monetization_willingness", "notes"} & payload.keys():
        setattr(row, key, payload[key])
    row.updated_at = _now()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def add_asset(session: Session, payload: dict[str, Any]) -> CapabilityAsset:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("asset name is required")
    row = CapabilityAsset(
        name=name,
        **{
            k: v
            for k, v in payload.items()
            if k in {"asset_type", "availability", "monetization_willingness", "estimated_reuse_effort_hours", "notes"}
        },
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def update_asset(session: Session, asset_id: str, payload: dict[str, Any]) -> CapabilityAsset:
    row = session.get(CapabilityAsset, asset_id)
    if row is None:
        raise ValueError("asset not found")
    for key in {
        "name",
        "asset_type",
        "availability",
        "monetization_willingness",
        "estimated_reuse_effort_hours",
        "notes",
    } & payload.keys():
        setattr(row, key, payload[key])
    row.updated_at = _now()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def candidate_payload(candidate: OpportunityCandidate) -> dict[str, Any]:
    out = _table_payload(candidate)
    for field in (
        "evidence_json",
        "assumptions_json",
        "matched_skills_json",
        "matched_assets_json",
        "violated_constraints_json",
        "required_activities_json",
        "rejection_reasons_json",
    ):
        out[field.removesuffix("_json")] = _loads(getattr(candidate, field))
    out["experiment"] = _loads_dict(candidate.experiment_json)
    try:
        out["score_components"] = json.loads(candidate.score_components_json or "{}")
    except json.JSONDecodeError:
        out["score_components"] = {}
    out["decision_brief"] = build_candidate_decision_brief(candidate)
    return out


def candidate_summary(candidate: OpportunityCandidate) -> dict[str, Any]:
    """List-card contract without eagerly sending evidence/history payloads."""
    out = candidate_payload(candidate)
    out["evidence"] = []
    out["assumptions"] = []
    out["score_components"] = {}
    out["quality_state"] = candidate_quality(candidate)
    return out


def _value_type(candidate: OpportunityCandidate, quality: str) -> str:
    if quality in {"NEEDS_EVIDENCE", "NEEDS_ECONOMICS", "DISCOVERED"}:
        return "validation"
    if candidate.category in {"capability_matched_growth", "productization", "income"}:
        return "income"
    if candidate.category in {
        "mortgage",
        "credit_card",
        "debt_paydown",
        "insurance",
        "savings",
        "subscription",
        "recurring",
    }:
        return "savings"
    return "cost_avoidance"


def _iso_date(value: Any) -> str | None:
    return value.date().isoformat() if hasattr(value, "date") else None


def _next_step(candidate: OpportunityCandidate, quality: str, missing: list[str]) -> str:
    experiment = _loads_dict(candidate.experiment_json)
    if experiment.get("next_decision"):
        return str(experiment["next_decision"]).strip()
    if candidate.lifecycle_status in {"accepted", "attempted"}:
        return "Complete the next owner action and record the observed outcome."
    if quality == "DECISION_GRADE" and candidate.category == "debt_paydown":
        return "Review the safe extra-payment amount and make the payment only if you choose."
    if quality == "NEEDS_EVIDENCE":
        return "Obtain one current source or quote that verifies the stated terms."
    if quality == "NEEDS_ECONOMICS":
        if candidate.category == "mortgage_refinance":
            return "Obtain one exact refinance quote with APR and closing costs."
        if candidate.category in {"credit_card", "debt_paydown"}:
            return "Verify the promotional APR, transfer fee, and promotion duration."
        if candidate.category in {"subscription", "recurring"}:
            return "Confirm whether the recurring charge can be canceled or downgraded."
        if candidate.category in {"capability_matched_growth", "productization", "income"}:
            return "Validate demand with one bounded experiment using the stated limits."
        return "Use Resolve next to complete the next factual validation step."
    if quality == "DECISION_GRADE":
        return "Review the evidence and accept to create the tracked action."
    if missing:
        return "Resolve the next missing fact before deciding."
    return "Review the evidence and choose the smallest reversible next action."


def build_candidate_decision_brief(candidate: OpportunityCandidate) -> dict[str, Any]:
    """Derive a complete, factual owner brief without filling unknowns."""
    quality = candidate_quality(candidate)
    expected = [candidate.expected_net_low, candidate.expected_net_high]
    probability = [candidate.probability_low, candidate.probability_high]
    missing: list[str] = []
    if any(value is None for value in expected):
        missing.append("expected_net_range")
    if any(value is None for value in probability) and candidate.category not in DETERMINISTIC_ECONOMIC_CATEGORIES:
        missing.append("probability_range")
    if candidate.required_capital is None:
        missing.append("required_capital")
    if candidate.capital_at_risk is None:
        missing.append("capital_at_risk")
    if candidate.setup_hours is None:
        missing.append("setup_hours")
    if candidate.recurring_hours_per_week is None:
        missing.append("recurring_effort")
    if candidate.time_to_value_days is None:
        missing.append("time_to_value")
    evidence = _loads(candidate.evidence_json)
    if not evidence:
        missing.append("supporting_evidence")

    why_now = "No time-sensitive reason identified."
    if candidate.lifecycle_status in {"accepted", "attempted"}:
        why_now = "This move is already in progress."
    elif candidate.expires_at is not None:
        why_now = f"The current candidate terms expire on {_iso_date(candidate.expires_at)}."
    elif quality in {"NEEDS_EVIDENCE", "NEEDS_ECONOMICS"}:
        why_now = "Validation is required before this candidate can be ranked safely."
    elif candidate.freshness_status == "current" and candidate.verification_status == "verified":
        why_now = "Current verified evidence is available for review."

    return {
        "quality_state": quality,
        "why_now": why_now,
        "value_type": _value_type(candidate, quality),
        "expected_net_low": candidate.expected_net_low,
        "expected_net_high": candidate.expected_net_high,
        "probability_low": candidate.probability_low,
        "probability_high": candidate.probability_high,
        "required_capital": candidate.required_capital,
        "capital_at_risk": candidate.capital_at_risk,
        "recurring_operating_cost": candidate.recurring_operating_cost,
        "liquidity_lockup_days": candidate.liquidity_lockup_days,
        "setup_hours": candidate.setup_hours,
        "recurring_hours_per_week": candidate.recurring_hours_per_week,
        "time_to_value_days": candidate.time_to_value_days,
        "value_horizon_days": candidate.value_horizon_days,
        "capability_fit_score": candidate.capability_fit_score,
        "scalability": candidate.scalability,
        "automation_potential": candidate.automation_potential,
        "reversibility": candidate.reversibility,
        "complexity": candidate.complexity,
        "downside_severity": candidate.downside_severity,
        "verification_status": candidate.verification_status,
        "freshness_status": candidate.freshness_status,
        "assumptions": _loads(candidate.assumptions_json),
        "missing_facts": missing,
        "next_step": _next_step(candidate, quality, missing),
        "safety_eligible": candidate.safety_eligible,
        "rank_score": candidate.rank_score,
        "score_components": _loads_dict(candidate.score_components_json),
        "experiment": _loads_dict(candidate.experiment_json),
    }


def candidate_quality(candidate: OpportunityCandidate) -> str:
    """Classify a candidate for decision-pool presentation without mutating it.

    Lifecycle is intentionally not enough: migrated offers and stale external
    observations remain useful history, but must not crowd the current queue.
    """
    if candidate.lifecycle_status in {"accepted", "attempted"}:
        return "ACTIONABLE"
    if candidate.lifecycle_status in TERMINAL:
        return "TERMINAL"
    if candidate.category == "financial_offer" or (candidate.source_label or "").lower().startswith(
        ("legacy", "native_superseded", "recurring_not_current", "owner_kept", "owner_not_recurring")
    ):
        return "ARCHIVAL"
    if candidate.freshness_status in {"stale", "expired"}:
        return "ARCHIVAL"
    if candidate.source_type == "researched_external" and candidate.verification_status != "verified":
        return "NEEDS_EVIDENCE"
    if "conflicting_validation_facts" in _loads(candidate.rejection_reasons_json):
        return "NEEDS_ECONOMICS"
    if _median(candidate.expected_net_low, candidate.expected_net_high) is None or (
        candidate.category not in DETERMINISTIC_ECONOMIC_CATEGORIES
        and _median(candidate.probability_low, candidate.probability_high) is None
    ):
        return "NEEDS_ECONOMICS"
    if candidate.qualification_status == "qualified" and candidate.safety_eligible:
        return "DECISION_GRADE"
    return "DISCOVERED"


def candidate_applicability(session: Session, candidate: OpportunityCandidate) -> dict[str, Any]:
    """Derive whether a candidate has the household context needed to matter."""
    category = candidate.category
    if category in {"cash_yield_benchmark", "savings"}:
        accounts = session.exec(select(LiquidAccount)).all()
        if not accounts:
            return {
                "status": "PREREQUISITE_MISSING",
                "reason_code": "liquid_account_required",
                "instruction": "Add the liquid account you actually want Ledger to compare.",
                "target_route": "/money?section=cash_on_hand",
            }
        return {"status": "APPLICABLE", "reason_code": None, "instruction": None, "target_route": None}
    if category == "mortgage_refinance" and not session.exec(select(MortgageAccount)).first():
        return {
            "status": "NOT_CURRENTLY_APPLICABLE",
            "reason_code": "mortgage_required",
            "instruction": "A refinance comparison needs a current mortgage.",
            "target_route": "/money?section=mortgage",
        }
    if category in {"credit_card", "debt_paydown"} and not (
        session.exec(select(CreditCard)).first() or session.exec(select(Debt)).first()
    ):
        return {
            "status": "NOT_CURRENTLY_APPLICABLE",
            "reason_code": "credit_card_required",
            "instruction": "A balance-transfer comparison needs an applicable card balance.",
            "target_route": "/money?section=credit_cards",
        }
    if category in {"capability_matched_growth", "productization", "income", "digital_product"}:
        skills = session.exec(select(CapabilitySkill)).all()
        assets = session.exec(select(CapabilityAsset)).all()
        has_capability = any(row.monetization_willingness and row.name.strip() for row in skills) or any(
            row.monetization_willingness and row.availability == "available" and row.name.strip() for row in assets
        )
        if not has_capability:
            return {
                "status": "NOT_CURRENTLY_APPLICABLE",
                "reason_code": "capability_required",
                "instruction": "Add an explicit monetizable skill or available asset before growth discovery.",
                "target_route": "/moves",
            }
    return {"status": "APPLICABLE", "reason_code": None, "instruction": None, "target_route": None}


def _task_for_candidate(session: Session, candidate: OpportunityCandidate) -> dict[str, Any] | None:
    """Return only owner-executable, factual work for one candidate."""
    applicability = candidate_applicability(session, candidate)
    if applicability["status"] != "APPLICABLE":
        return None
    quality = candidate_quality(candidate)
    if quality == "NEEDS_EVIDENCE":
        return {
            "task_type": "EXTERNAL_FACT",
            "reason_code": "supporting_evidence_missing",
            "candidate_id": candidate.id,
            "fact_type": None,
            "instruction": "Obtain one current source or quote that verifies the stated terms.",
            "action_kind": "OPEN_CANDIDATE_VALIDATION",
            "target_route": "/moves",
            "candidates_helped": 1,
            "prerequisite_status": "APPLICABLE",
        }
    from app.core.v2_validation import validation_payload

    payload = validation_payload(session, candidate)
    derived = {
        "expected_net_range",
        "probability_range",
        "required_capital",
        "capital_at_risk",
        "setup_hours",
        "recurring_effort",
        "time_to_value",
    }
    descriptions = {
        "comparison_account_id": "Select the liquid account you actually want Ledger to compare.",
        "comparison_fee": "Enter a factual transfer or account fee if one applies.",
        "liquidity_lockup_days": "Enter the factual number of days funds would be restricted.",
        "quoted_apr": "Obtain the exact refinance quote APR.",
        "quoted_term_months": "Obtain the exact refinance quote term.",
        "closing_costs": "Obtain the exact refinance closing costs and points.",
        "quoted_payment": "Provide the quoted principal-and-interest payment if supplied.",
        "quote_expires_at": "Provide the quote expiration date if supplied.",
        "promotional_apr": "Obtain the verified promotional APR.",
        "promotion_duration_months": "Obtain the promotional duration.",
        "transfer_fee_percent": "Obtain the transfer fee percentage, including any stated minimum.",
        "transfer_fee_fixed": "Obtain the fixed transfer fee if the source states one.",
        "transfer_fee_minimum": "Obtain the minimum transfer fee if a percentage fee has one.",
        "post_promo_apr": "Obtain the post-promotion APR if it is stated.",
        "max_transferable_amount": "Obtain the stated maximum transferable amount.",
        "can_cancel": "Confirm whether the recurring charge can be cancelled or downgraded.",
        "new_recurring_amount": "Enter the verified replacement recurring amount.",
        "cancellation_fee": "Enter the factual cancellation fee if one applies.",
        "stated_compensation": "Enter compensation only when it is explicitly stated by the source.",
        "platform_fee": "Enter the factual platform fee if one applies.",
        "required_expense": "Enter the known required expense if one is stated.",
        "owner_setup_hours": "Estimate the setup hours you would actually accept.",
        "owner_recurring_hours": "Estimate the recurring hours per week you would actually accept.",
    }
    for fact_type in payload.get("missing_fact_types") or []:
        if fact_type in derived:
            continue
        return {
            "task_type": "OWNER_FACT",
            "reason_code": f"missing_{fact_type}",
            "candidate_id": candidate.id,
            "fact_type": fact_type,
            "instruction": descriptions.get(
                fact_type, "Provide the specific factual input requested for this candidate."
            ),
            "action_kind": "OPEN_CANDIDATE_VALIDATION",
            "target_route": "/moves",
            "candidates_helped": 1,
            "prerequisite_status": "APPLICABLE",
        }
    experiment = _loads_dict(candidate.experiment_json)
    if experiment and experiment.get("next_decision"):
        return {
            "task_type": "VALIDATION_EXPERIMENT",
            "reason_code": "validation_experiment_pending",
            "candidate_id": candidate.id,
            "fact_type": None,
            "instruction": str(experiment["next_decision"]).strip(),
            "action_kind": "OPEN_CANDIDATE_EXPERIMENT",
            "target_route": "/moves",
            "candidates_helped": 1,
            "prerequisite_status": "APPLICABLE",
        }
    return None


def resolve_next_validation_task(session: Session) -> dict[str, Any]:
    """Choose one executable validation task; never delegate derived economics."""
    candidates = decision_pool(session)
    cash_prereq = [
        row for row in candidates if candidate_applicability(session, row)["status"] == "PREREQUISITE_MISSING"
    ]
    if cash_prereq:
        first = candidate_applicability(session, cash_prereq[0])
        return {
            "task_type": "HOUSEHOLD_PREREQUISITE",
            "reason_code": first["reason_code"],
            "candidate_id": None,
            "fact_type": None,
            "instruction": first["instruction"],
            "action": first["instruction"],
            "action_kind": "OPEN_HOUSEHOLD_EDITOR",
            "target_route": first["target_route"],
            "candidates_helped": len(cash_prereq),
            "prerequisite_status": "PREREQUISITE_MISSING",
        }
    tasks = [task for row in candidates if (task := _task_for_candidate(session, row)) is not None]
    if tasks:
        tasks.sort(key=lambda task: (-int(task["candidates_helped"]), str(task["candidate_id"] or "")))
        tasks[0]["action"] = tasks[0]["instruction"]
        return tasks[0]
    return {
        "task_type": "NO_ACTIONABLE_VALIDATION",
        "reason_code": "no_executable_task",
        "candidate_id": None,
        "fact_type": None,
        "instruction": "No actionable validation task is currently available.",
        "action": "No actionable validation task is currently available.",
        "action_kind": "NONE",
        "target_route": None,
        "candidates_helped": 0,
        "prerequisite_status": "NONE",
    }


DEPENDENT_CATEGORIES_BY_DOMAIN: dict[str, frozenset[str]] = {
    "account": frozenset({"cash_yield_benchmark", "savings"}),
    "cash": frozenset({"cash_yield_benchmark", "savings"}),
    "mortgage": frozenset({"mortgage_refinance"}),
    "card": frozenset({"credit_card", "debt_paydown"}),
    "debt": frozenset({"credit_card", "debt_paydown"}),
    "capability": frozenset({"capability_matched_growth", "productization", "income", "digital_product"}),
    "cashflow": frozenset({"subscription", "recurring"}),
}


def sync_household_recurring_cost_moves(session: Session) -> dict[str, int]:
    """Refresh recurring-charge candidates from current transaction history.

    This intentionally uses the single-candidate adapter rather than the bulk
    V1 normalizer, which also reconciles native debt targets.  Recurring scans
    must never retire an unrelated debt move.
    """
    from app.core.opportunity_engine import _subscription_opportunities

    findings = _subscription_opportunities(session)
    active_identities = {
        _identity({"category": "subscription", "canonical_identity": item.get("canonical_identity", "")})
        for item in findings
        if item.get("canonical_identity")
    }
    created_or_updated = 0
    for item in findings:
        candidate = normalize_v1_opportunity(session, item)
        created_or_updated += 1
        if candidate.source_label in {"owner_kept", "owner_not_recurring"}:
            continue
    for candidate in session.exec(select(OpportunityCandidate)).all():
        if candidate.category not in {"subscription", "recurring"} or candidate.lifecycle_status in TERMINAL:
            continue
        if (
            candidate.identity_key not in active_identities
            and candidate.source_label not in {"owner_kept", "owner_not_recurring"}
        ):
            candidate.source_label = "recurring_not_current"
            candidate.expected_gross_low = candidate.expected_gross_high = None
            candidate.expected_net_low = candidate.expected_net_high = None
            candidate.rank_score = None
            candidate.safety_eligible = False
            candidate.qualification_status = "pending"
            session.add(candidate)
    session.commit()
    observe_recurring_outcomes(session)
    return {"patterns": len(findings), "candidates_updated": created_or_updated}


def recurring_observation_start(session: Session, candidate: OpportunityCandidate) -> datetime | None:
    """Return the durable timestamp when the owner initiated the candidate action."""
    action = session.exec(
        select(FinancialAction)
        .where(FinancialAction.opportunity_candidate_id == candidate.id)
        .order_by(FinancialAction.created_at.asc())
    ).first()
    if action is not None:
        return action.created_at
    attempted = session.exec(
        select(OpportunityTransition)
        .where(
            OpportunityTransition.candidate_id == candidate.id,
            OpportunityTransition.to_status == "attempted",
        )
        .order_by(OpportunityTransition.created_at.asc())
    ).first()
    return attempted.created_at if attempted is not None else None


def observe_recurring_outcomes(session: Session) -> int:
    """Record a recurring cancellation only after a cadence-aware absence window."""
    from app.core.financial_snapshot import (
        annual_occurrences,
        current_recurring_evidence,
        normalize_transaction_merchant,
    )
    from app.core.v2_outcome_learning import record_candidate_outcome

    observed_count = 0
    transactions = session.exec(select(Transaction)).all()
    for candidate in session.exec(select(OpportunityCandidate)).all():
        if (
            candidate.category not in {"subscription", "recurring"}
            or candidate.lifecycle_status not in {"accepted", "attempted"}
        ):
            continue
        recurrence = current_recurring_evidence(candidate)
        if recurrence is None:
            continue
        action_start = recurring_observation_start(session, candidate)
        if action_start is None:
            # Acceptance/confirmation is not proof that the owner actually
            # cancelled the charge.  Wait for a durable action timestamp.
            continue
        merchant = str(recurrence.get("merchant_key") or "")
        last_raw = recurrence.get("last_transaction_date")
        cadence_days = float(recurrence.get("cadence_days") or 0)
        if not merchant or not last_raw or cadence_days <= 0:
            continue
        try:
            last_date = date.fromisoformat(str(last_raw))
        except ValueError:
            continue
        observation_date = date.today()
        action_date = action_start.date()
        elapsed_days = max(0, (observation_date - action_date).days)
        terminal = elapsed_days >= 365
        horizon_end = min(observation_date, action_date + timedelta(days=365))
        expected_dates: list[date] = []
        occurrence_number = 1
        while occurrence_number <= 1000:
            expected = last_date + timedelta(days=round(occurrence_number * cadence_days))
            if expected > horizon_end:
                break
            if expected > action_date:
                expected_dates.append(expected)
            occurrence_number += 1
        observed_occurrences = len(expected_dates)
        minimum_intervals = 1 if terminal and recurrence.get("cadence") == "annual" else 2
        if observed_occurrences < minimum_intervals:
            continue
        if any(
            action_date < tx.transaction_date <= observation_date
            and normalize_transaction_merchant(tx.description or tx.category) == merchant
            for tx in transactions
        ):
            continue
        existing = session.exec(
            select(OpportunityOutcomeObservation).where(
                OpportunityOutcomeObservation.candidate_id == candidate.id,
            )
        ).first()
        baseline = float(recurrence.get("avg_amount") or 0)
        observed_intervals = min(
            observed_occurrences,
            int(round(float(annual_occurrences(recurrence.get("cadence")) or observed_occurrences))),
        )
        # A recurring response stores cancellation_fee as a versioned owner fact;
        # use the existing validation map without copying projected annual value.
        from app.core.v2_validation import _facts_map

        facts = _facts_map(session, candidate.id)
        if facts.get("new_recurring_amount") is not None:
            # Disappearance of the old merchant does not prove savings after a
            # switch; replacement transaction evidence is required.
            continue
        # An absent actual-fee fact is unknown, not a factual zero. Preserve an
        # explicit zero separately so observed net is only derived when the
        # direct cost is actually known.
        actual_fee = facts.get("actual_cancellation_fee")
        fee = max(0.0, float(actual_fee)) if actual_fee is not None else None
        observed_gross = round(baseline * observed_intervals, 2)
        observed_net = round(observed_gross - fee, 2) if fee is not None else None
        if existing and existing.outcome_status == "completed":
            continue
        if (
            existing
            and float(existing.actual_gross_value or 0) >= observed_gross
            and (fee is None or not terminal)
        ):
            continue
        # Without a factual actual fee, retain observed effect evidence but
        # remain nonterminal so a later owner/transaction fact can establish a
        # truthful signed net result. This avoids manufacturing zero cost at
        # the 365-day horizon.
        outcome_status = "completed" if terminal and fee is not None else "in_progress"
        outcome_data: dict[str, Any] = {
            "outcome_status": outcome_status,
            "actual_gross_value": observed_gross,
            "outcome_reason": (
                "Recurring charge absent through the 365-day observation horizon."
                if terminal
                else "Recurring charge absent for the currently observed cadence intervals."
            ),
        }
        if fee is not None:
            outcome_data["actual_direct_cost"] = fee
            outcome_data["actual_net_value"] = observed_net
        record_candidate_outcome(session, candidate, outcome_data)
        observed_count += 1
    return observed_count


def respond_to_recurring_candidate(
    session: Session,
    candidate: OpportunityCandidate,
    response: str,
    *,
    new_recurring_amount: float | None = None,
    replacement_cadence: str | None = None,
    cancellation_fee: float | None = None,
) -> OpportunityCandidate:
    """Record the one novice-facing recurring-charge decision."""
    from app.core.v2_validation import save_fact

    response = response.strip().lower().replace("-", "_")
    if candidate.category not in {"subscription", "recurring"}:
        raise ValueError("candidate is not a recurring charge")
    if response not in {"keep", "cancel", "switch", "not_recurring", "not_sure"}:
        raise ValueError("invalid recurring response")
    if response in {"keep", "not_recurring"}:
        target = "dismissed"
        if candidate.lifecycle_status != target:
            prior = candidate.lifecycle_status
            candidate.lifecycle_status = target
            candidate.source_label = "owner_kept" if response == "keep" else "owner_not_recurring"
            candidate.qualification_status = "pending"
            candidate.safety_eligible = False
            candidate.rank_score = None
            candidate.updated_at = _now()
            session.add(candidate)
            session.add(
                OpportunityTransition(
                    candidate_id=candidate.id,
                    from_status=prior,
                    to_status=target,
                    reason=f"owner_{response}",
                )
            )
            session.commit()
        return candidate
    if response == "not_sure":
        return candidate
    save_fact(
        session,
        candidate,
        fact_type="can_cancel",
        value=True,
        source_type="owner_confirmed_statement",
        verification_status="owner_confirmed",
    )
    if response == "switch":
        if new_recurring_amount is None:
            raise ValueError("replacement recurring amount is required to switch")
        save_fact(
            session,
            candidate,
            fact_type="new_recurring_amount",
            value=new_recurring_amount,
            source_type="owner_confirmed_statement",
            verification_status="owner_confirmed",
        )
        if replacement_cadence is not None:
            save_fact(
                session,
                candidate,
                fact_type="replacement_cadence",
                value=replacement_cadence,
                source_type="owner_confirmed_statement",
                verification_status="owner_confirmed",
            )
    elif new_recurring_amount is not None:
        save_fact(
            session,
            candidate,
            fact_type="new_recurring_amount",
            value=new_recurring_amount,
            source_type="owner_confirmed_statement",
            verification_status="owner_confirmed",
        )
    if cancellation_fee is not None:
        save_fact(
            session,
            candidate,
            fact_type="cancellation_fee",
            value=cancellation_fee,
            source_type="owner_confirmed_statement",
            verification_status="owner_confirmed",
        )
    session.refresh(candidate)
    return candidate


def _reevaluate_categories(
    session: Session,
    categories: set[str],
    cashflow: dict[str, Any] | None = None,
) -> int:
    """Recompute selected candidate families without triggering a rank pass."""
    from app.core.v2_validation import enrich_candidate_economics

    changed = 0
    for candidate in session.exec(select(OpportunityCandidate)).all():
        if candidate.category not in categories or candidate.lifecycle_status in TERMINAL:
            continue
        enrich_candidate_economics(session, candidate, cashflow=cashflow)
        qualify_candidate(session, candidate, cashflow)
        changed += 1
    return changed


def reevaluate_household_dependents(session: Session, categories: set[str]) -> int:
    """Backward-compatible category refresh with one final rank pass."""
    changed = _reevaluate_categories(session, categories)
    _ranked_candidates(session, sync_native=True)
    return changed


def refresh_household_decision_dependents(
    session: Session,
    changed_domains: set[str],
    cashflow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Refresh all decision families affected by one canonical household write.

    This is the single composition boundary for household-triggered decision
    refreshes. It preserves D023/D024 category-specific enrichment while also
    synchronizing the D026 native debt move against the same D001 cashflow.
    """
    categories: set[str] = set()
    for domain in changed_domains:
        categories.update(DEPENDENT_CATEGORIES_BY_DOMAIN.get(domain, ()))
    current_cashflow = cashflow or calculate_cashflow(session)
    recurring = (
        sync_household_recurring_cost_moves(session)
        if "cashflow" in changed_domains
        else {"patterns": 0, "candidates_updated": 0}
    )
    dependent_count = _reevaluate_categories(session, categories, current_cashflow)
    native = sync_household_native_value_moves(session, current_cashflow, rank=False)
    ranked = _ranked_candidates(session, sync_native=False)
    return {
        "changed_domains": sorted(changed_domains),
        "categories": sorted(categories),
        "dependents_refreshed": dependent_count,
        "native": native,
        "recurring": recurring,
        "ranked_count": len(ranked),
    }


def sync_household_native_value_moves(
    session: Session, cashflow: dict[str, Any] | None = None, *, rank: bool = True
) -> dict[str, Any]:
    """Synchronize the one current native debt move against live D001 state."""
    from app.core.financial_snapshot import build_financial_snapshot
    from app.core.opportunity_engine import detect_opportunities

    current_cashflow = cashflow or calculate_cashflow(session)
    snapshot = build_financial_snapshot(session)
    snapshot = snapshot.model_copy(
        update={
            "cashflow": current_cashflow,
            "money_state": dict(current_cashflow.get("money_state") or {}),
        }
    )
    findings = [row for row in detect_opportunities(snapshot, session) if row.get("category") == "debt_paydown"]
    candidates = normalize_v1_opportunities(session, findings)
    from app.core.v2_validation import enrich_candidate_economics

    for candidate in candidates:
        enrich_candidate_economics(session, candidate, cashflow=current_cashflow)
        qualify_candidate(session, candidate, current_cashflow)
    if rank:
        _ranked_candidates(session, sync_native=False)
    return {
        "cashflow": current_cashflow,
        "findings": len(findings),
        "candidates": len(candidates),
    }


def decision_pool(session: Session) -> list[OpportunityCandidate]:
    """Return non-archival, non-terminal candidates for current Moves views."""
    return [
        row
        for row in session.exec(select(OpportunityCandidate)).all()
        if candidate_quality(row) not in {"ARCHIVAL", "TERMINAL"}
        and candidate_applicability(session, row)["status"] != "NOT_CURRENTLY_APPLICABLE"
    ]


def candidate_history(session: Session) -> list[OpportunityCandidate]:
    """Return terminal and archival candidates for deliberate history views."""
    return [
        row
        for row in session.exec(select(OpportunityCandidate)).all()
        if candidate_quality(row) in {"ARCHIVAL", "TERMINAL"}
    ]


def opportunity_quality_summary(session: Session) -> dict[str, Any]:
    rows = session.exec(select(OpportunityCandidate)).all()
    states = ("ARCHIVAL", "DISCOVERED", "NEEDS_EVIDENCE", "NEEDS_ECONOMICS", "DECISION_GRADE", "ACTIONABLE", "TERMINAL")
    counts: dict[str, int] = {state: 0 for state in states}
    zero_reasons: dict[str, int] = {}
    for row in rows:
        state = candidate_quality(row)
        counts[state] += 1
        if (row.rank_score or 0) == 0:
            reasons = _loads(row.rejection_reasons_json)
            if not reasons:
                unknown = (
                    _median(row.expected_net_low, row.expected_net_high) is None
                    or _median(row.probability_low, row.probability_high) is None
                )
                reasons = ["economics_or_probability_unknown"] if unknown else ["below_threshold_or_unqualified"]
            for reason in reasons:
                zero_reasons[str(reason)] = zero_reasons.get(str(reason), 0) + 1
    return {
        "total": len(rows),
        "counts": counts,
        "current_decision_inventory": sum(
            counts[state]
            for state in ("DISCOVERED", "NEEDS_EVIDENCE", "NEEDS_ECONOMICS", "DECISION_GRADE", "ACTIONABLE")
        ),
        "zero_score_reasons": zero_reasons,
    }


def validation_bottleneck_summary(session: Session) -> dict[str, Any]:
    """Count decision blockers and expose only an executable next task."""
    counts = {
        "missing_evidence": 0,
        "missing_expected_net": 0,
        "missing_probability": 0,
        "missing_comparison_rate_or_apy": 0,
        "missing_fees": 0,
        "missing_effort": 0,
        "missing_capital_or_risk": 0,
        "owner_input_required": 0,
        "validation_experiment_pending": 0,
    }
    for candidate in decision_pool(session):
        quality = candidate_quality(candidate)
        if quality not in {"NEEDS_EVIDENCE", "NEEDS_ECONOMICS", "DISCOVERED"}:
            continue
        brief = build_candidate_decision_brief(candidate)
        missing = set(brief.get("missing_facts") or [])
        if "supporting_evidence" in missing:
            counts["missing_evidence"] += 1
        if "expected_net_range" in missing:
            counts["missing_expected_net"] += 1
        if "probability_range" in missing:
            counts["missing_probability"] += 1
        if candidate.category in {"cash_yield_benchmark", "savings"} and (
            "expected_net_range" in missing or "required_capital" in missing
        ):
            counts["missing_comparison_rate_or_apy"] += 1
        if candidate.category in {"credit_card", "cash_yield_benchmark", "mortgage_refinance"} and (
            "expected_net_range" in missing
        ):
            counts["missing_fees"] += 1
        if {"setup_hours", "recurring_effort", "time_to_value"} & missing:
            counts["missing_effort"] += 1
        if {"required_capital", "capital_at_risk"} & missing:
            counts["missing_capital_or_risk"] += 1
        if quality == "NEEDS_EVIDENCE":
            counts["owner_input_required"] += 1
        if brief.get("experiment"):
            counts["validation_experiment_pending"] += 1
    task = resolve_next_validation_task(session)
    return {
        "counts": counts,
        "next_best_validation_task": task,
        "next_task": task["instruction"],
    }


def realized_value_summary(session: Session) -> dict[str, Any]:
    """Summarize observed outcomes only; projections never count as realized."""
    from app.db.models import OpportunityOutcomeObservation

    candidates = session.exec(select(OpportunityCandidate)).all()
    observations = session.exec(select(OpportunityOutcomeObservation)).all()
    completed = [row for row in observations if row.outcome_status == "completed"]
    candidates_by_id = {row.id: row for row in candidates}
    net_values = [float(row.actual_net_value) for row in completed if row.actual_net_value is not None]
    observed_savings = [
        float(row.actual_net_value)
        for row in completed
        if row.actual_net_value is not None
        and candidates_by_id.get(row.candidate_id, None) is not None
        and candidates_by_id[row.candidate_id].category not in {"capability_matched_growth", "productization", "income"}
    ]
    observed_income = [
        float(row.actual_net_value)
        for row in completed
        if row.actual_net_value is not None
        and candidates_by_id.get(row.candidate_id, None) is not None
        and candidates_by_id[row.candidate_id].category in {"capability_matched_growth", "productization", "income"}
    ]
    return {
        "accepted_moves": sum(
            row.lifecycle_status in {"accepted", "attempted", "outcome_recorded"} for row in candidates
        ),
        "attempted_moves": sum(row.lifecycle_status in {"attempted", "outcome_recorded"} for row in candidates),
        "completed_outcomes": len(completed),
        "observed_net_savings": round(sum(value for value in observed_savings), 2),
        "observed_net_income": round(sum(value for value in observed_income), 2),
        "observed_total_realized_value": round(sum(net_values), 2),
        "actual_owner_hours": round(sum(float(row.actual_setup_hours or 0) for row in completed), 2),
        "projected_values_excluded": True,
    }


def ingest_candidate(session: Session, payload: dict[str, Any]) -> OpportunityCandidate:
    payload = dict(payload)
    experiment = payload.pop("validation_experiment", None)
    if experiment is not None:
        required_experiment_fields = {
            "hypothesis",
            "max_capital",
            "max_hours",
            "success_metric",
            "failure_metric",
            "review_date",
            "stop_loss",
            "next_decision",
        }
        if not isinstance(experiment, dict) or required_experiment_fields - set(experiment):
            raise ValueError(
                "validation experiment requires hypothesis, limits, metrics, review, stop-loss, and next decision"
            )
        if float(experiment["max_capital"]) < 0 or float(experiment["max_hours"]) < 0:
            raise ValueError("validation experiment limits must be non-negative")
        payload["experiment_json"] = json.dumps(experiment, sort_keys=True)
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("candidate title is required")
    source_type = str(payload.get("source_type") or "owner_supplied")
    if source_type not in {"deterministic_internal", "owner_supplied", "researched_external", "ai_hypothesis"}:
        raise ValueError("invalid source_type")
    identity = _identity(payload)
    existing = session.exec(select(OpportunityCandidate).where(OpportunityCandidate.identity_key == identity)).first()
    evidence = list(payload.get("evidence") or [])
    if existing:
        prior_evidence = _loads(existing.evidence_json)
        new_evidence = [item for item in evidence if item not in prior_evidence]
        source_record = {
            "source_type": source_type,
            "source_url": payload.get("source_url", ""),
            "source_label": payload.get("source_label", ""),
        }
        if source_record not in prior_evidence and any(source_record.values()):
            new_evidence.append(source_record)
        existing.evidence_json = json.dumps(prior_evidence + new_evidence)
        # An explicit, independently verified update can enrich a prior owner or
        # AI hypothesis.  It never rewrites the candidate's original origin.
        if payload.get("verification_status") == "verified" and source_type != "ai_hypothesis":
            existing.verification_status = "verified"
        if payload.get("freshness_status"):
            existing.freshness_status = str(payload["freshness_status"])
        if source_type == "researched_external" and not payload.get("materially_distinct_terms"):
            # A new observation of one stable external identity can refresh
            # supported terms; source evidence remains append-only.
            for key in {
                "summary",
                "expected_gross_low",
                "expected_gross_high",
                "estimated_direct_cost",
                "expected_net_low",
                "expected_net_high",
                "probability_low",
                "probability_high",
                "required_capital",
                "capital_at_risk",
                "recurring_operating_cost",
                "time_to_value_days",
                "value_horizon_days",
                "setup_hours",
                "recurring_hours_per_week",
                "liquidity_lockup_days",
                "expires_at",
                "fetched_at",
            } & payload.keys():
                setattr(existing, key, payload[key])
        elif source_type == "deterministic_internal":
            # Native findings use a stable canonical identity and refresh
            # their household-derived economics in place as balances or safe
            # capacity change.  This prevents one candidate per daily run.
            for key in {
                "summary",
                "expected_gross_low",
                "expected_gross_high",
                "estimated_direct_cost",
                "expected_net_low",
                "expected_net_high",
                "required_capital",
                "capital_at_risk",
                "recurring_operating_cost",
                "time_to_value_days",
                "value_horizon_days",
                "setup_hours",
                "recurring_hours_per_week",
                "liquidity_lockup_days",
            } & payload.keys():
                setattr(existing, key, payload[key])
        existing.updated_at = _now()
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing
    fields = set(OpportunityCandidate.model_fields) - {
        "id",
        "identity_key",
        "title",
        "source_type",
        "created_at",
        "updated_at",
        "evidence_json",
        "assumptions_json",
        "lifecycle_status",
        "qualification_status",
        "verification_status",
        "freshness_status",
    }
    row = OpportunityCandidate(
        identity_key=identity,
        title=title,
        source_type=source_type,
        lifecycle_status="unverified" if source_type in {"researched_external", "ai_hypothesis"} else "discovered",
        qualification_status="unverified" if source_type in {"researched_external", "ai_hypothesis"} else "discovered",
        verification_status=(
            str(payload.get("verification_status"))
            if payload.get("verification_status") in {"unverified", "verified", "rejected"}
            else ("verified" if source_type == "deterministic_internal" else "unverified")
        ),
        freshness_status=str(payload.get("freshness_status") or "unknown"),
        evidence_json=json.dumps(evidence),
        assumptions_json=json.dumps(payload.get("assumptions") or []),
        **{key: value for key, value in payload.items() if key in fields},
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    session.add(
        OpportunityTransition(
            candidate_id=row.id,
            from_status="",
            to_status=row.lifecycle_status,
            reason="ingested",
            evidence_json=json.dumps(evidence),
        )
    )
    session.commit()
    return row


def normalize_legacy_financial_offers(session: Session) -> dict[str, int]:
    """Normalize factual legacy offers without inventing economics.

    Historical offers remain untouched.  Each source row is represented as an
    unverified external candidate keyed by its legacy id, so rerunning this
    adapter only enriches evidence and never creates a duplicate.
    """
    created = 0
    already_migrated = 0
    conflicts = 0
    errors = 0
    rows = session.exec(select(FinancialOffer)).all()
    for offer in rows:
        title = (offer.offer_name or f"{offer.provider} {offer.product_type}").strip()
        if not title:
            errors += 1
            continue
        payload: dict[str, Any] = {
            "title": title,
            "summary": offer.eligibility_notes or offer.notes or "Legacy financial offer; verify terms before acting.",
            "category": "financial_offer",
            "source_type": "researched_external" if offer.intake_source == "research" else "owner_supplied",
            "source_url": offer.source_url or "",
            "source_label": offer.provider,
            "external_identity": f"financial_offer:{offer.id}",
            "verification_status": "verified" if offer.evidence_notes or offer.source_url else "unverified",
            "freshness_status": (
                "stale" if offer.expiration_date and offer.expiration_date < datetime.now(UTC).date() else "current"
            ),
            "expires_at": (
                datetime.combine(offer.expiration_date, datetime.min.time()) if offer.expiration_date else None
            ),
            "fetched_at": offer.fetched_at,
            "evidence": [
                {
                    "source_type": "financial_offer",
                    "source_id": offer.id,
                    "source_url": offer.source_url or "",
                    "provider": offer.provider,
                    "confidence": offer.confidence,
                }
            ],
        }
        try:
            before = session.exec(
                select(OpportunityCandidate).where(OpportunityCandidate.identity_key == _identity(payload))
            ).first()
            ingest_candidate(session, payload)
            if before is None:
                created += 1
            else:
                already_migrated += 1
        except ValueError:
            conflicts += 1
        except Exception:
            errors += 1
    return {"created": created, "already_migrated": already_migrated, "conflicts": conflicts, "errors": errors}


def _transition(
    session: Session, candidate: OpportunityCandidate, target: str, reason: str, *, force: bool = False
) -> OpportunityCandidate:
    current = candidate.lifecycle_status
    if target not in LIFECYCLE or (
        not force and (current in TERMINAL or target not in TRANSITIONS.get(current, set()))
    ):
        raise ValueError(f"invalid lifecycle transition: {current} -> {target}")
    candidate.lifecycle_status = target
    candidate.updated_at = _now()
    session.add(candidate)
    session.add(OpportunityTransition(candidate_id=candidate.id, from_status=current, to_status=target, reason=reason))
    session.commit()
    session.refresh(candidate)
    return candidate


def transition_candidate(
    session: Session, candidate: OpportunityCandidate, target: str, reason: str = ""
) -> OpportunityCandidate:
    if target == "dismissed" and reason and not reason.startswith("owner_"):
        # Preserve old callers' free-text note while keeping an explicit owner
        # signal. Automatic safety/evidence failures never use this route.
        reason = f"owner_other:{reason}"
    elif target == "dismissed" and not reason:
        reason = "owner_other"
    row = _transition(session, candidate, target, reason)
    if target == "accepted":
        from app.core.v2_outcome_learning import freeze_prediction

        freeze_prediction(session, row)
    return row


def _median(low: float | None, high: float | None) -> float | None:
    if low is None or high is None:
        return None
    return (float(low) + float(high)) / 2


def _has_material_validation_conflict(session: Session, candidate: OpportunityCandidate) -> bool:
    facts = session.exec(
        select(OpportunityValidationFact).where(
            OpportunityValidationFact.candidate_id == candidate.id,
            OpportunityValidationFact.status == "current",
        )
    ).all()
    for fact_type in {row.fact_type for row in facts}:
        values = {row.factual_value_json for row in facts if row.fact_type == fact_type}
        if len(values) > 1:
            return True
    evidence_claims = {
        "quoted_apr": {"rate", "apr", "annual_percentage_rate"},
        "promotional_apr": {"rate", "apr", "annual_percentage_rate"},
    }
    evidence = session.exec(
        select(OpportunityEvidence).where(
            OpportunityEvidence.candidate_id == candidate.id,
            OpportunityEvidence.status == "current",
        )
    ).all()
    for fact_type, claim_types in evidence_claims.items():
        owner_rows = [row for row in facts if row.fact_type == fact_type]
        if not owner_rows:
            continue
        try:
            owner_value = float(json.loads(owner_rows[-1].factual_value_json))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        for row in evidence:
            if row.claim_type not in claim_types:
                continue
            if row.expires_at is not None and row.expires_at <= _now():
                continue
            try:
                evidence_value = float(json.loads(row.factual_value_json or "null"))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if abs(evidence_value - owner_value) > 0.05:
                return True
    return False


def qualify_candidate(
    session: Session, candidate: OpportunityCandidate, cashflow: dict[str, Any] | None = None
) -> dict[str, Any]:
    cf = cashflow or calculate_cashflow(session)
    money = cf.get("money_state") or {}
    profile = get_profile(session)
    reasons: list[str] = []
    required = float(candidate.required_capital or 0)
    at_risk = float(candidate.capital_at_risk or 0)
    experiment = _loads_dict(candidate.experiment_json)
    if experiment:
        if float(experiment.get("max_capital") or 0) > float(money.get("experimental_capital") or 0):
            reasons.append("experiment_max_capital_exceeds_experimental_capital")
        if profile.max_setup_hours is not None and float(experiment.get("max_hours") or 0) > profile.max_setup_hours:
            reasons.append("experiment_max_hours_exceed_owner_limit")
    if required > float(money.get("deployable_surplus") or 0):
        reasons.append("required_capital_exceeds_deployable_surplus")
    if at_risk > float(money.get("experimental_capital") or 0):
        reasons.append("capital_at_risk_exceeds_experimental_capital")
    if candidate.liquidity_lockup_days and money.get("risk_windows"):
        reasons.append("liquidity_lockup_conflicts_with_risk_window")
    if candidate.source_type == "researched_external" and (
        candidate.verification_status != "verified" or candidate.freshness_status == "stale"
    ):
        reasons.append("external_evidence_not_verified_fresh")
    if candidate.source_type == "ai_hypothesis":
        observed_claims = {
            row.claim_type
            for row in session.exec(
                select(OpportunityEvidence).where(
                    OpportunityEvidence.candidate_id == candidate.id,
                    OpportunityEvidence.status == "current",
                )
            ).all()
        }
        if not {"buyer_demand", "comparable_price", "required_cost"}.issubset(observed_claims):
            reasons.append("ai_hypothesis_requires_independent_evidence")
    if _has_material_validation_conflict(session, candidate):
        reasons.append("conflicting_validation_facts")
    if _median(candidate.expected_net_low, candidate.expected_net_high) is None:
        reasons.append("economics_unknown")
    if (
        candidate.category not in DETERMINISTIC_ECONOMIC_CATEGORIES
        and _median(candidate.probability_low, candidate.probability_high) is None
    ):
        reasons.append("probability_unknown")
    if profile.max_setup_hours is not None and (candidate.setup_hours or 0) > profile.max_setup_hours:
        reasons.append("setup_hours_exceed_owner_limit")
    if (
        profile.max_recurring_hours_per_week is not None
        and (candidate.recurring_hours_per_week or 0) > profile.max_recurring_hours_per_week
    ):
        reasons.append("recurring_hours_exceed_owner_limit")
    activities = set(_loads(candidate.required_activities_json)) | set(_loads(candidate.violated_constraints_json))
    if profile.customer_interaction_willingness is False and "customer_interaction" in activities:
        reasons.append("customer_interaction_refused")
    if profile.sales_outreach_willingness is False and "sales_outreach" in activities:
        reasons.append("sales_outreach_refused")
    if profile.physical_labor_willingness is False and "physical_labor" in activities:
        reasons.append("physical_labor_refused")
    refused = {str(item).lower() for item in _loads(profile.refused_activities_json)}
    if refused & {str(item).lower() for item in activities}:
        reasons.append("owner_refused_activity")
    value = _median(candidate.expected_net_low, candidate.expected_net_high) or 0
    effort = (candidate.setup_hours or 0) + 4 * (candidate.recurring_hours_per_week or 0)
    linear = (
        candidate.continued_labor_dependence >= 0.8
        and candidate.scalability <= 0.2
        and candidate.automation_potential <= 0.2
    )
    if linear and value / max(1, effort) < max(10, float(profile.minimum_meaningful_payoff or 0) / 10):
        reasons.append("generic_low_leverage_labor")
    hard_reasons = [reason for reason in reasons if reason not in PENDING_QUALIFICATION_REASONS]
    active = candidate.lifecycle_status in {"accepted", "attempted"}
    eligible = not reasons
    candidate.safety_eligible = eligible
    candidate.rejection_reasons_json = json.dumps(reasons)
    if eligible:
        candidate.qualification_status = "qualified"
    elif active:
        # D020 contract: accepted/attempted owner work is never silently
        # demoted because later evidence or economics became incomplete.
        candidate.qualification_status = candidate.qualification_status or "pending"
    elif hard_reasons:
        candidate.qualification_status = "rejected"
    else:
        candidate.qualification_status = "pending"
    candidate.updated_at = _now()
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    if eligible and candidate.lifecycle_status == "discovered":
        _transition(session, candidate, "researched", "owner-supplied qualification review")
    if eligible and candidate.lifecycle_status == "unverified":
        _transition(session, candidate, "researched", "evidence verification complete")
    if eligible and candidate.lifecycle_status == "researched":
        _transition(session, candidate, "qualified", "qualification passed")
    elif not eligible and hard_reasons and candidate.lifecycle_status not in TERMINAL and not active:
        _transition(session, candidate, "rejected", "; ".join(reasons))
    return {"eligible": eligible, "reasons": reasons, "money_state": money, "candidate": candidate_payload(candidate)}


def rank_candidate(candidate: OpportunityCandidate) -> dict[str, Any]:
    expected = _median(candidate.expected_net_low, candidate.expected_net_high)
    probability = _median(candidate.probability_low, candidate.probability_high)
    deterministic = candidate.category in DETERMINISTIC_ECONOMIC_CATEGORIES
    if expected is None or (probability is None and not deterministic):
        return {"score": 0.0, "reason": "economics_or_probability_unknown"}
    if deterministic and expected <= 0:
        return {"score": 0.0, "expected_value": round(float(expected), 2), "reason": "non_positive_supported_net"}
    value = max(0.0, expected if deterministic else expected * float(probability))
    effort_known = candidate.setup_hours is not None and candidate.recurring_hours_per_week is not None
    effort_semantics = "known"
    effort: float | None
    if effort_known:
        effort = float(candidate.setup_hours or 0) + 4 * float(candidate.recurring_hours_per_week or 0)
    elif candidate.category == "debt_paydown":
        # A debt payment is a bounded, straightforward owner action, but its
        # duration is not observed. Keep the qualitative treatment explicit
        # instead of fabricating hours.
        effort = None
        effort_semantics = "minimal_owner_action"
    else:
        effort = None
        effort_semantics = "unknown"
    evidence_quality = (
        1.0
        if candidate.verification_status == "verified" and candidate.freshness_status != "stale"
        else 0.6
        if candidate.source_type == "owner_supplied"
        else 0.25
    )
    efficiency = 0.0 if effort is None else min(1.0, value / max(1, effort) / 100)
    if effort_semantics == "minimal_owner_action":
        efficiency = min(1.0, value / 100) * 0.8
    benefit = min(1.0, value / 1000) * 0.30 + efficiency * 0.20
    qualities = (
        candidate.capability_fit_score
        + candidate.scalability
        + candidate.repeatability
        + candidate.automation_potential
        + candidate.reversibility
    ) / 5
    penalties = (
        min(1.0, effort / 40) * 0.15 if effort is not None else 0.0
        + min(1.0, float(candidate.capital_at_risk or 0) / 1000) * 0.10
        + min(1.0, (candidate.liquidity_lockup_days or 0) / 90) * 0.10
        + (candidate.complexity + candidate.downside_severity + candidate.continued_labor_dependence) / 3 * 0.15
    )
    score = max(0.0, min(100.0, (benefit + qualities * 0.35 + evidence_quality * 0.05 - penalties) * 100))
    return {
        "score": round(score, 2),
        "expected_value": round(value, 2),
        "effort_hours_4_weeks": round(effort, 2) if effort is not None else None,
        "effort_semantics": effort_semantics,
        "benefit": round(benefit, 3),
        "quality": round(qualities, 3),
        "evidence_quality": evidence_quality,
        "penalty": round(penalties, 3),
        "version": "v2.3",
        "economic_semantics": "deterministic_supported_net" if deterministic else "probability_adjusted_net",
    }


def _ranked_candidates(session: Session, *, sync_native: bool = True) -> list[dict[str, Any]]:
    if sync_native:
        sync_household_native_value_moves(session)
    # Portfolio-wide defense: any candidate that commits money or locks
    # liquidity is rechecked against the current D001 state at read/rank time.
    # This is deliberately bounded to capital-sensitive rows and never invokes
    # external research.
    from app.core.v2_validation import enrich_candidate_economics
    current_cashflow = calculate_cashflow(session)
    for candidate in session.exec(select(OpportunityCandidate)).all():
        experiment = _loads_dict(candidate.experiment_json)
        needs_live_safety = bool(
            float(candidate.required_capital or 0) > 0
            or float(candidate.capital_at_risk or 0) > 0
            or int(candidate.liquidity_lockup_days or 0) > 0
            or float(experiment.get("max_capital") or 0) > 0
        )
        if needs_live_safety and candidate.lifecycle_status not in TERMINAL:
            enrich_candidate_economics(session, candidate, cashflow=current_cashflow)
            qualify_candidate(session, candidate, current_cashflow)
    out = []
    for candidate in session.exec(select(OpportunityCandidate)).all():
        if candidate_quality(candidate) in {"ARCHIVAL", "TERMINAL"}:
            continue
        if candidate_applicability(session, candidate)["status"] != "APPLICABLE":
            continue
        if candidate.qualification_status != "qualified" or not candidate.safety_eligible:
            continue
        components = rank_candidate(candidate)
        from app.core.v2_outcome_learning import apply_calibration

        components = apply_calibration(session, candidate, components)
        candidate.rank_score = components["score"]
        candidate.score_components_json = json.dumps(components)
        session.add(candidate)
        out.append(candidate_payload(candidate))
    session.commit()
    return sorted(out, key=lambda row: float(row.get("rank_score") or 0), reverse=True)


def ranked_candidates(session: Session) -> list[dict[str, Any]]:
    """Rank only candidates synchronized against current household truth."""
    return _ranked_candidates(session)


def top_moves(session: Session) -> list[dict[str, Any]]:
    return [row for row in ranked_candidates(session) if float(row.get("rank_score") or 0) >= 20][:3]


def create_candidate_action(session: Session, candidate: OpportunityCandidate) -> FinancialAction:
    if candidate.lifecycle_status not in {"accepted", "attempted"} or not candidate.safety_eligible:
        raise ValueError("only accepted qualified candidates can create actions")
    existing = session.exec(
        select(FinancialAction).where(
            FinancialAction.opportunity_candidate_id == candidate.id,
            FinancialAction.status.in_(["open", "in_progress"]),
        )
    ).first()
    if existing:
        return existing
    action = FinancialAction(
        title=candidate.title,
        action_type="run_experiment",
        opportunity_candidate_id=candidate.id,
        linked_entity_type="opportunity_candidate",
        linked_entity_id=candidate.id,
        expected_total_savings=float(candidate.expected_net_low or 0),
        notes="Candidate-originated action",
    )
    session.add(action)
    session.commit()
    session.refresh(action)
    from app.core.v2_outcome_learning import link_prediction_to_action

    link_prediction_to_action(session, candidate, action)
    if candidate.lifecycle_status == "accepted":
        _transition(session, candidate, "attempted", "action created")
    return action


OWNER_DISMISSAL_REASON_CODES = frozenset(
    {
        "owner_irrelevant",
        "owner_too_much_work",
        "owner_timing",
        "owner_capital",
        "owner_weak_evidence",
        "owner_other",
    }
)


def accept_candidate_action(session: Session, candidate: OpportunityCandidate) -> FinancialAction:
    """Accept an eligible candidate and create its one idempotent action.

    This is intentionally the one semantic handoff used by the V2 UI.  It
    reuses qualification, audited lifecycle transitions, prediction freezing,
    and the existing action idempotency contract instead of duplicating any of
    those rules in an HTTP route or frontend.
    """
    if candidate.lifecycle_status not in {"accepted", "attempted"}:
        result = qualify_candidate(session, candidate)
        if not result["eligible"]:
            raise ValueError("candidate is not eligible: " + "; ".join(result["reasons"]))
        if candidate.lifecycle_status == "qualified":
            _transition(session, candidate, "proposed", "ready for owner decision")
        if candidate.lifecycle_status == "proposed":
            transition_candidate(session, candidate, "accepted", "owner accepted")
    return create_candidate_action(session, candidate)


def dismiss_candidate(
    session: Session, candidate: OpportunityCandidate, reason_code: str, note: str = ""
) -> OpportunityCandidate:
    """Record a semantic owner decision without teaching from safety failures."""
    if reason_code not in OWNER_DISMISSAL_REASON_CODES:
        raise ValueError("invalid dismissal reason")
    if candidate.lifecycle_status == "dismissed":
        return candidate
    if candidate.lifecycle_status == "qualified":
        _transition(session, candidate, "proposed", "ready for owner decision")
    if candidate.lifecycle_status not in {"proposed", "accepted", "attempted"}:
        raise ValueError("candidate is not available for owner dismissal")
    reason = reason_code if not note else f"{reason_code}:{note.strip()[:500]}"
    return transition_candidate(session, candidate, "dismissed", reason)


def normalize_v1_opportunity(session: Session, opportunity: dict[str, Any]) -> OpportunityCandidate:
    """Map a deterministic V1 finding into the one V2 candidate contract.

    Existing estimates and verification requirements are preserved verbatim;
    this adapter adds no external facts or synthetic economics.
    """
    annual = opportunity.get("estimated_yearly_savings")
    if annual in (None, ""):
        annual = float(opportunity.get("estimated_monthly_savings") or 0) * 12
    requires_verification = bool(opportunity.get("requires_manual_verification", True))
    math = opportunity.get("math") if isinstance(opportunity.get("math"), dict) else {}
    category = (
        "subscription"
        if opportunity.get("category") == "subscriptions"
        else opportunity.get("category", "other")
    )
    factual_fields = {
        "expected_gross_low",
        "expected_gross_high",
        "estimated_direct_cost",
        "expected_net_low",
        "expected_net_high",
        "required_capital",
        "capital_at_risk",
        "recurring_operating_cost",
        "time_to_value_days",
        "value_horizon_days",
        "setup_hours",
        "recurring_hours_per_week",
        "liquidity_lockup_days",
        "canonical_identity",
    }
    direct = {key: opportunity[key] for key in factual_fields if key in opportunity}
    if "expected_net_low" not in direct and annual:
        direct["expected_net_low"] = annual
        direct["expected_net_high"] = annual
    # Native findings carry their own deterministic economics in the math
    # payload.  External/V1 findings remain conservative and unknown by
    # default; only explicitly supplied factual fields cross this boundary.
    if opportunity.get("source_type") == "deterministic_internal":
        for key in factual_fields - set(direct):
            if key in math:
                direct[key] = math[key]
    probability_fields = {
        "probability_low": opportunity.get("probability_low"),
        "probability_high": opportunity.get("probability_high"),
    }
    if opportunity.get("source_type") == "deterministic_internal" and category == "debt_paydown":
        probability_fields = {"probability_low": None, "probability_high": None}
    return ingest_candidate(
        session,
        {
            "title": opportunity.get("title", "V1 opportunity"),
            "summary": opportunity.get("why", ""),
            "category": category,
            "source_type": "deterministic_internal",
            "source_label": "opportunity_engine",
            "verification_status": "unverified" if requires_verification else "verified",
            "freshness_status": "unknown" if requires_verification else "current",
            **direct,
            **probability_fields,
            "evidence": [
                {
                    "v1_source": "opportunity_engine",
                    "sources": opportunity.get("sources", []),
                    "math": opportunity.get("math", {}),
                    "requires_manual_verification": requires_verification,
                }
            ],
            "assumptions": opportunity.get("warnings", []),
        },
    )


def normalize_v1_opportunities(session: Session, opportunities: list[dict[str, Any]]) -> list[OpportunityCandidate]:
    """Persist all suitable V1 deterministic findings through the V2 adapter."""
    # Retire the pre-D026 plural spelling in place.  This is a derived category
    # correction only; evidence, transitions, and historical rows remain.
    for row in session.exec(select(OpportunityCandidate)).all():
        if row.category == "subscriptions" and row.lifecycle_status not in TERMINAL:
            row.category = "subscription"
            row.identity_key = _identity({"category": "subscription", "title": row.title})
            row.updated_at = _now()
            session.add(row)
    session.commit()
    native_identities = {
        _identity(
            {
                "category": "debt_paydown",
                "title": opportunity.get("title", ""),
                "canonical_identity": opportunity.get("canonical_identity", ""),
            }
        )
        for opportunity in opportunities
        if opportunity.get("category") == "debt_paydown" and opportunity.get("canonical_identity")
    }
    for row in session.exec(select(OpportunityCandidate)).all():
        if (
            row.category == "debt_paydown"
            and row.lifecycle_status not in TERMINAL
            and row.identity_key not in native_identities
        ):
            # A previously safe native action may become unavailable when
            # deployable capacity or the underlying balance changes.  Keep the
            # row for history, but clear its stale economics so it cannot rank.
            row.expected_gross_low = None
            row.expected_gross_high = None
            row.expected_net_low = None
            row.expected_net_high = None
            row.required_capital = None
            row.capital_at_risk = None
            row.rank_score = None
            row.safety_eligible = False
            row.qualification_status = "pending"
            row.rejection_reasons_json = json.dumps(["insufficient_deployable_surplus_or_balance"])
            row.source_label = "native_superseded"
            row.updated_at = _now()
            session.add(row)
        elif (
            row.category == "debt_paydown"
            and row.identity_key in native_identities
            and row.source_label == "native_superseded"
        ):
            row.source_label = ""
            session.add(row)
    session.commit()
    return [normalize_v1_opportunity(session, opportunity) for opportunity in opportunities if opportunity.get("title")]
