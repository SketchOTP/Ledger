"""D003 bounded external discovery, claim provenance, and D002 handoff."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from app.core.cashflow_engine import calculate_cashflow
from app.core.v2_opportunities import (
    PENDING_QUALIFICATION_REASONS,
    candidate_quality,
    get_profile,
    ingest_candidate,
    profile_completeness,
    qualify_candidate,
    ranked_candidates,
    top_moves,
)
from app.db.models import (
    CapabilityAsset,
    CapabilitySkill,
    CreditCard,
    LiquidAccount,
    MortgageAccount,
    OpportunityCandidate,
    OpportunityEvidence,
    OpportunityOutcomeObservation,
    OpportunityResearchRun,
    OpportunityTransition,
    ScheduledJob,
)
from app.research.providers.base import OpportunityDiscovery, OpportunityDiscoveryResult, ResearchProvider
from app.research.providers.fred_opportunity import FredMortgageOpportunityProvider
from app.research.providers.github_issue_demand import GitHubIssueDemandProvider
from app.research.providers.public_market_evidence import PublicMarketEvidenceProvider
from app.research.providers.remote_ok import RemoteOkProvider
from app.research.providers.treasury_rates import TreasuryYieldProvider

SCOPES = frozenset({"all", "financial_optimization", "growth"})
SOURCE_QUALITY = frozenset(
    {"official_primary", "reputable_secondary", "marketplace_or_listing", "community_or_anecdotal", "unknown"}
)
DEFAULT_LIMITS = {
    "max_themes": 3,
    "max_queries": 6,
    "max_results_per_query": 3,
    "max_requests_per_provider": 2,
    "timeout_seconds": 5,
    "max_retries": 1,
}
PROVIDER_FRESHNESS_HOURS = {
    "fred_mortgage_opportunity": 24 * 14,
    "treasury_yield_benchmark": 48,
    "remote_ok_demand": 24 * 7,
    "github_issue_demand": 24 * 7,
}


def build_research_context_from_snapshot(snapshot: Any) -> dict[str, Any]:
    """Map the canonical snapshot to provider-safe research context."""
    return {
        "cashflow": snapshot.cashflow,
        "borrower": snapshot.borrower,
        "raw_mortgages": snapshot.raw_mortgages,
        "raw_bills": snapshot.raw_bills,
        "raw_debts": snapshot.raw_debts,
        "raw_credit_cards": snapshot.raw_credit_cards,
    }


def research_facts_for_snapshot(session: Session) -> list[dict[str, Any]]:
    """Convert current D003 claim evidence into math-safe snapshot facts.

    Only structured, current claims are exposed to deterministic household
    economics.  Narrative text is never interpreted as a financial fact.
    """
    facts: list[dict[str, Any]] = []
    candidates = {row.id: row for row in session.exec(select(OpportunityCandidate)).all()}
    for evidence in session.exec(select(OpportunityEvidence)).all():
        if evidence.status != "current" or not evidence.candidate_id:
            continue
        candidate = candidates.get(evidence.candidate_id)
        if candidate is None or candidate.category not in {"mortgage_refinance", "credit_card"}:
            continue
        try:
            value = json.loads(evidence.factual_value_json or "null")
        except json.JSONDecodeError:
            continue
        if evidence.claim_type not in {"rate", "apr", "annual_percentage_rate"}:
            continue
        try:
            apr = float(value)
        except (TypeError, ValueError):
            continue
        facts.append(
            {
                "product_type": (
                    "mortgage_refinance" if candidate.category == "mortgage_refinance" else "balance_transfer"
                ),
                "estimated_apr": apr,
                "source_url": evidence.source_url,
                "provider": evidence.provider_id,
                "confidence": evidence.evidence_strength,
                "verified": candidate.verification_status == "verified",
            }
        )
    return facts


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _band(amount: Any) -> str:
    value = max(0.0, float(amount or 0))
    if value == 0:
        return "none"
    if value < 250:
        return "under_250"
    if value < 1_000:
        return "250_to_999"
    if value < 5_000:
        return "1000_to_4999"
    return "5000_or_more"


def _effort_band(hours: float | None) -> str:
    if hours is None:
        return "unspecified"
    if hours <= 4:
        return "low"
    if hours <= 12:
        return "moderate"
    return "high"


def build_research_brief(session: Session, cashflow: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create the only provider-facing context: coarse and free of private notes."""
    money = (cashflow or calculate_cashflow(session)).get("money_state") or {}
    profile = get_profile(session)
    skills = [
        row.name.strip()[:80]
        for row in session.exec(select(CapabilitySkill)).all()
        if row.monetization_willingness and row.name.strip()
    ]
    assets = [
        row.name.strip()[:80]
        for row in session.exec(select(CapabilityAsset)).all()
        if row.monetization_willingness and row.availability == "available" and row.name.strip()
    ]
    completeness = profile_completeness(
        profile,
        [{"name": row, "monetization_willingness": True} for row in skills],
        [{"name": row, "availability": "available", "monetization_willingness": True} for row in assets],
    )
    candidates = session.exec(select(OpportunityCandidate)).all()
    active_categories = sorted({row.category for row in candidates if row.qualification_status == "qualified"})
    dismissed_categories = sorted(
        {row.category for row in candidates if row.lifecycle_status in {"dismissed", "rejected"}}
    )
    risk_windows = money.get("risk_windows") or []
    return {
        "financial_constraints": {
            "deployable_surplus_band": _band(money.get("deployable_surplus")),
            "experimental_capital_band": _band(money.get("experimental_capital")),
            "near_term_liquidity_risk": bool(risk_windows),
        },
        "capability": {
            "skills": skills,
            "assets": assets,
            "setup_effort": _effort_band(profile.max_setup_hours),
            "recurring_effort": _effort_band(profile.max_recurring_hours_per_week),
            "preferred_effort_level": profile.preferred_effort_level or "unspecified",
            "customer_interaction_willingness": profile.customer_interaction_willingness,
            "sales_outreach_willingness": profile.sales_outreach_willingness,
            "physical_labor_willingness": profile.physical_labor_willingness,
            "prefers_async": profile.prefers_async,
            "prefers_automated": profile.prefers_automated,
            "prefers_low_touch": profile.prefers_low_touch,
            "prefers_recurring_income": profile.prefers_recurring_income,
            "prefers_scalable_income": profile.prefers_scalable_income,
            "allows_one_time_income": profile.allows_one_time_income,
            "desired_time_to_value_days": profile.desired_time_to_value_days,
            "minimum_meaningful_payoff_band": _band(profile.minimum_meaningful_payoff),
            "max_preferred_capital_band": _band(profile.max_preferred_capital),
        },
        "existing_state": {
            "qualified_categories": active_categories,
            "dismissed_categories": dismissed_categories,
        },
        "personalization_limited": not bool(skills or assets),
        "capability_completeness": completeness,
    }


def build_research_plan(brief: dict[str, Any], *, scope: str = "all") -> dict[str, Any]:
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {sorted(SCOPES)}")
    themes: list[str] = []
    requests: list[dict[str, str]] = []
    if scope in {"all", "financial_optimization"}:
        themes.append("financial_optimization")
        requests.append({"theme": "financial_optimization", "request_class": "official_mortgage_benchmark"})
    capabilities = brief.get("capability") or {}
    if scope in {"all", "growth"} and (capabilities.get("skills") or capabilities.get("assets")):
        if capabilities.get("prefers_scalable_income") or capabilities.get("prefers_automated"):
            themes.append("productization")
        themes.append("capability_matched_growth")
        for theme in themes:
            if theme != "financial_optimization":
                requests.append({"theme": theme, "request_class": "public_capability_demand"})
    themes = themes[:DEFAULT_LIMITS["max_themes"]]
    requests = requests[:DEFAULT_LIMITS["max_queries"]]
    return {
        "selected_themes": themes,
        "requests": requests,
        "limits": dict(DEFAULT_LIMITS),
        "personalization_limited": bool(brief.get("personalization_limited")),
        "provider_notes": [
            "Growth requests use a bounded public demand-signal provider; listings are not earnings claims."
        ] if any(item["request_class"] == "public_capability_demand" for item in requests) else [],
    }


def _capability_query_plan(brief: dict[str, Any]) -> dict[str, list[str]]:
    """Select at most three explicit capability concepts per provider."""
    capability = brief.get("capability") or {}
    concepts: list[str] = []
    for value in [*(capability.get("skills") or []), *(capability.get("assets") or [])]:
        normalized = " ".join(str(value).strip().split())[:80]
        if normalized and normalized.casefold() not in {item.casefold() for item in concepts}:
            concepts.append(normalized)
    concepts = concepts[:3]
    return {
        "remote_ok_demand": concepts,
        "github_issue_demand": concepts,
    }


def _fresh_provider_evidence(session: Session, provider_id: str, now: datetime) -> bool:
    freshness_hours = PROVIDER_FRESHNESS_HOURS.get(provider_id)
    if freshness_hours is None:
        return False
    rows = session.exec(
        select(OpportunityEvidence).where(
            OpportunityEvidence.provider_id == provider_id,
            OpportunityEvidence.status == "current",
        )
    ).all()
    if not rows:
        return False
    cutoff = now - timedelta(hours=freshness_hours)
    return any(
        (row.retrieved_at or row.created_at) >= cutoff
        and (row.expires_at is None or row.expires_at > now)
        for row in rows
    )


def default_discovery_providers() -> list[ResearchProvider]:
    return [
        FredMortgageOpportunityProvider(),
        TreasuryYieldProvider(),
        RemoteOkProvider(),
        GitHubIssueDemandProvider(),
    ]


def research_hypothesis_claims(
    session: Session,
    hypotheses: list[dict[str, Any]],
    cashflow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a bounded claim-specific pass for the current hypothesis IDs.

    This deliberately reuses the D003 evidence writer. Unsupported price and
    cost claims stay unresolved rather than being inferred from a demand hit.
    """
    limited = [item for item in hypotheses[:3] if item.get("candidate_id")]
    run = OpportunityResearchRun(
        trigger="hypothesis_validation",
        scope="growth",
        brief_json=json.dumps({"hypotheses": limited}, sort_keys=True),
        plan_json=json.dumps(
            {"claim_types": ["buyer_demand", "comparable_price", "required_cost"], "max_hypotheses": 3},
            sort_keys=True,
        ),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    provider = PublicMarketEvidenceProvider()
    demand_fallback = GitHubIssueDemandProvider()
    acquired = 0
    claims: dict[str, dict[str, Any]] = {}
    for hypothesis in limited:
        candidate_id = str(hypothesis["candidate_id"])
        claim_results = provider.discover_hypothesis(hypothesis)
        if not claim_results.get("buyer_demand") or not claim_results["buyer_demand"].discoveries:
            claim_results["buyer_demand"] = demand_fallback.discover_hypothesis(hypothesis)
        candidate_claims: set[str] = set()
        for claim_type, result in claim_results.items():
            if result.discoveries:
                candidate_claims.add(claim_type)
            for discovery in result.discoveries[:3]:
                records, _, _ = _evidence_for_discovery(session, run, result.provider_id, discovery, candidate_id)
                acquired += len(records)
                candidate_claims.update(record.claim_type for record in records)
        claims[candidate_id] = {
            "acquired": sorted(candidate_claims),
            "unresolved": sorted({"buyer_demand", "comparable_price", "required_cost"} - candidate_claims),
        }
    run.results_examined = sum(len(item.get("acquired", [])) for item in claims.values())
    run.request_count = len(limited)
    run.status = "success"
    run.completed_at = _now()
    run.updated_at = _now()
    run.provider_status_json = json.dumps(
        [{"provider_id": provider.provider_id, "hypothesis_specific": True, "claims": claims}]
    )
    session.add(run)
    session.commit()
    return {
        "hypotheses_researched": len(limited),
        "claims_acquired": acquired,
        "claims": claims,
        "provider": provider.provider_id,
    }


def _has_mortgage(session: Session) -> bool:
    return session.exec(select(MortgageAccount)).first() is not None


def _run_payload(row: OpportunityResearchRun) -> dict[str, Any]:
    def load(raw: str, fallback: Any) -> Any:
        try:
            return json.loads(raw or "")
        except json.JSONDecodeError:
            return fallback

    return {
        "id": row.id,
        "trigger": row.trigger,
        "scope": row.scope,
        "status": row.status,
        "brief": load(row.brief_json, {}),
        "plan": load(row.plan_json, {}),
        "providers": load(row.provider_status_json, []),
        "errors": load(row.error_json, []),
        "results_examined": row.results_examined,
        "candidates_created": row.candidates_created,
        "candidates_merged": row.candidates_merged,
        "candidates_prefiltered": row.candidates_prefiltered,
        "candidates_qualified": row.candidates_qualified,
        "top_moves_changed": row.top_moves_changed,
        "request_count": row.request_count,
        "material_state_fingerprint": row.material_state_fingerprint,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


def list_research_runs(session: Session, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = list(session.exec(select(OpportunityResearchRun).order_by(OpportunityResearchRun.started_at.desc())).all())
    return [_run_payload(row) for row in rows[:limit]]


def _provider_result(
    provider: ResearchProvider, context: dict[str, Any], plan: dict[str, Any]
) -> tuple[OpportunityDiscoveryResult, int]:
    retries = int((plan.get("limits") or {}).get("max_retries", 0))
    attempts = 0
    result: OpportunityDiscoveryResult | None = None
    for _ in range(retries + 1):
        attempts += 1
        result = provider.discover(context, plan)
        if not result.error or result.rate_limited:
            break
    assert result is not None
    return result, attempts


def _json_list(raw: str) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
        return value if isinstance(value, list) else []
    except json.JSONDecodeError:
        return []


def _provider_error_class(result: OpportunityDiscoveryResult) -> str | None:
    if not result.error:
        return None
    if result.rate_limited:
        return "rate_limited"
    message = str(result.error).lower()
    if any(token in message for token in ("credential", "configuration", "api key", "token")):
        return "configuration_missing"
    if "timeout" in message:
        return "timeout"
    if "http" in message or "status" in message:
        return "http_error"
    return "provider_error"


def _is_prefiltered(
    discovery: OpportunityDiscovery, brief: dict[str, Any], profile: Any | None = None
) -> str | None:
    if discovery.candidate_fields.get("expired"):
        return "expired_external_opportunity"
    activities = set(discovery.required_activities)
    capability = brief.get("capability") or {}
    refused = {str(item).strip().lower() for item in _json_list(getattr(profile, "refused_activities_json", "[]"))}
    if "physical_labor" in activities and (
        capability.get("prefers_low_touch") or capability.get("physical_labor_willingness") is False
    ):
        return "prefiltered_physical_labor_preference"
    if "customer_interaction" in activities and capability.get("customer_interaction_willingness") is False:
        return "prefiltered_customer_interaction_refused"
    if "sales_outreach" in activities and capability.get("sales_outreach_willingness") is False:
        return "prefiltered_sales_outreach_refused"
    if refused & {str(item).lower() for item in activities}:
        return "prefiltered_owner_refused_activity"
    if profile is not None and profile.allows_one_time_income is False:
        income_type = str(discovery.candidate_fields.get("income_type") or "").lower()
        if income_type in {"one_time", "one-time", "project", "project_based"}:
            return "prefiltered_one_time_income_disallowed"
    if discovery.candidate_fields.get("geographic_eligible") is False or discovery.candidate_fields.get(
        "geography_eligible"
    ) is False:
        return "prefiltered_geographic_ineligible"
    setup_hours = discovery.candidate_fields.get("setup_hours")
    if profile is not None and profile.max_setup_hours is not None and setup_hours is not None:
        if float(setup_hours) > float(profile.max_setup_hours):
            return "prefiltered_setup_effort_exceeds_limit"
    recurring_hours = discovery.candidate_fields.get("recurring_hours_per_week")
    if profile is not None and profile.max_recurring_hours_per_week is not None and recurring_hours is not None:
        if float(recurring_hours) > float(profile.max_recurring_hours_per_week):
            return "prefiltered_recurring_effort_exceeds_limit"
    required_capital = discovery.candidate_fields.get("required_capital")
    if profile is not None and profile.max_preferred_capital is not None and required_capital is not None:
        if float(required_capital) > float(profile.max_preferred_capital):
            return "prefiltered_capital_exceeds_owner_preference"
    minimum_payoff = profile.minimum_meaningful_payoff if profile is not None else None
    expected_net_high = discovery.candidate_fields.get("expected_net_high")
    if minimum_payoff is not None and expected_net_high is not None:
        if float(expected_net_high) < float(minimum_payoff):
            return "prefiltered_below_minimum_meaningful_payoff"
    return None


def _evidence_for_discovery(
    session: Session,
    run: OpportunityResearchRun,
    provider_id: str,
    discovery: OpportunityDiscovery,
    candidate_id: str,
) -> tuple[list[OpportunityEvidence], list[dict[str, Any]], int]:
    records: list[OpportunityEvidence] = []
    payload: list[dict[str, Any]] = []
    deduped = 0
    for claim in discovery.claims:
        if claim.source_quality not in SOURCE_QUALITY:
            raise ValueError("invalid source quality")
        raw = json.dumps(
            {"url": discovery.source_url, "claim_type": claim.claim_type, "value": claim.value, "units": claim.units},
            sort_keys=True,
            default=str,
        )
        fingerprint = hashlib.sha256(raw.encode()).hexdigest()
        existing = session.exec(
            select(OpportunityEvidence).where(
                OpportunityEvidence.candidate_id == candidate_id,
                OpportunityEvidence.provider_id == provider_id,
                OpportunityEvidence.claim_type == claim.claim_type,
                OpportunityEvidence.content_fingerprint == fingerprint,
                OpportunityEvidence.status == "current",
            )
        ).first()
        if existing is not None:
            deduped += 1
            continue
        record = OpportunityEvidence(
            research_run_id=run.id,
            provider_id=provider_id,
            source_url=discovery.source_url,
            source_title=discovery.source_title,
            source_domain=discovery.source_domain,
            publisher=discovery.publisher,
            source_quality=claim.source_quality,
            claim_type=claim.claim_type,
            factual_value_json=json.dumps(claim.value, default=str),
            units=claim.units,
            evidence_strength=claim.evidence_strength,
            supporting_snippet=claim.supporting_snippet[:500],
            content_fingerprint=fingerprint,
            retrieved_at=_now(),
            published_at=_parse_datetime(claim.published_at),
            expires_at=_now() + timedelta(hours=max(1, claim.freshness_hours)),
        )
        session.add(record)
        session.flush()
        records.append(record)
        payload.append(
            {
                "evidence_id": record.id,
                "source_url": record.source_url,
                "source_quality": record.source_quality,
                "claim_type": record.claim_type,
                "value": claim.value,
                "units": record.units,
                "retrieved_at": record.retrieved_at.isoformat(),
                "expires_at": record.expires_at.isoformat() if record.expires_at else None,
            }
        )
    return records, payload, deduped


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=None)
    except ValueError:
        return None


def _normalize_discovery(
    session: Session,
    run: OpportunityResearchRun,
    provider_id: str,
    discovery: OpportunityDiscovery,
    brief: dict[str, Any],
    cashflow: dict[str, Any],
) -> tuple[bool, bool, OpportunityCandidate | None, str | None, int]:
    prefiltered = _is_prefiltered(discovery, brief, get_profile(session))
    if prefiltered:
        return False, False, None, prefiltered, 0
    candidate_fields = dict(discovery.candidate_fields)
    candidate_fields.update(
        {
            "title": discovery.title,
            "summary": discovery.summary,
            "category": discovery.category,
            "source_type": "researched_external",
            "source_url": discovery.source_url,
            "source_label": discovery.publisher or discovery.source_title,
            "external_identity": discovery.external_identity,
            "canonical_identity": discovery.canonical_identity,
            "required_activities_json": json.dumps(discovery.required_activities),
        }
    )
    existing_ids = {row.id for row in session.exec(select(OpportunityCandidate)).all()}
    candidate = ingest_candidate(session, candidate_fields)
    records, evidence, evidence_deduped = _evidence_for_discovery(session, run, provider_id, discovery, candidate.id)
    prior_evidence = json.loads(candidate.evidence_json or "[]")
    candidate.evidence_json = json.dumps(prior_evidence + evidence)
    session.add(candidate)
    for record in records:
        record.candidate_id = candidate.id
        session.add(record)
    session.commit()
    qualification = qualify_candidate(session, candidate, cashflow)
    return candidate.id not in existing_ids, bool(qualification["eligible"]), candidate, None, evidence_deduped


def run_opportunity_research(
    session: Session,
    *,
    scope: str = "all",
    trigger: str = "manual",
    providers: list[ResearchProvider] | None = None,
    cashflow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a small, failure-isolated discovery pass; never creates fake results."""
    brief = build_research_brief(session, cashflow)
    from app.core.v2_outcome_learning import material_state_fingerprint, research_targeting

    plan = build_research_plan(brief, scope=scope)
    plan["research_targeting"] = research_targeting(session)
    plan["cohort"] = "post_d021r1_canonical"
    plan["capability_query_counts"] = {key: len(value) for key, value in _capability_query_plan(brief).items()}
    cf = cashflow or calculate_cashflow(session)
    current_fingerprint = material_state_fingerprint(session, brief)
    previous_run = session.exec(
        select(OpportunityResearchRun).order_by(OpportunityResearchRun.started_at.desc())
    ).first()
    material_state_changed = previous_run is None or previous_run.material_state_fingerprint != current_fingerprint
    before = [row["id"] for row in top_moves(session)]
    run = OpportunityResearchRun(
        trigger=trigger,
        scope=scope,
        brief_json=json.dumps(brief),
        plan_json=json.dumps(plan),
        material_state_fingerprint=current_fingerprint,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    statuses: list[dict[str, Any]] = []
    errors: list[str] = []
    configured = providers if providers is not None else default_discovery_providers()
    context = {
        "research_brief": brief,
        "has_mortgage": _has_mortgage(session),
        "has_liquid_accounts": bool(session.exec(select(LiquidAccount)).first()),
        "has_credit_cards": bool(session.exec(select(CreditCard)).first()),
        "capability_query": _capability_query_plan(brief),
    }
    max_provider_requests = int(plan["limits"]["max_requests_per_provider"])
    for provider in configured:
        if (
            not material_state_changed
            and provider.provider_id in PROVIDER_FRESHNESS_HOURS
            and _fresh_provider_evidence(session, provider.provider_id, _now())
        ):
            statuses.append(
                {
                    "provider_id": provider.provider_id,
                    "attempted": False,
                    "attempts": 0,
                    "request_count": 0,
                    "discovery_count": 0,
                    "results_examined": 0,
                    "accepted_discoveries": 0,
                    "prefiltered": 0,
                    "candidates_created": 0,
                    "candidates_merged": 0,
                    "evidence_deduped": 0,
                    "validation_pending": 0,
                    "needs_evidence": 0,
                    "needs_economics": 0,
                    "qualified": 0,
                    "skipped": True,
                    "skipped_reason": "SKIPPED_FRESH_EVIDENCE",
                    "rate_limited": False,
                    "configuration_missing": False,
                    "error": None,
                    "error_class": None,
                }
            )
            continue
        try:
            result, attempts = _provider_result(provider, context, plan)
        except Exception as exc:  # provider isolation must leave the run auditable
            result, attempts = OpportunityDiscoveryResult(provider_id=provider.provider_id, error=str(exc)), 1
        requests_used = min(result.request_count * attempts, max_provider_requests)
        error_class = _provider_error_class(result)
        provider_status = {
            "provider_id": result.provider_id,
            "attempted": not result.skipped,
            "attempts": attempts,
            "request_count": requests_used,
            "discovery_count": len(result.discoveries),
            "results_examined": 0,
            "accepted_discoveries": 0,
            "prefiltered": 0,
            "candidates_created": 0,
            "candidates_merged": 0,
            "evidence_deduped": 0,
            "validation_pending": 0,
            "needs_evidence": 0,
            "needs_economics": 0,
            "qualified": 0,
            "skipped": result.skipped,
            "skipped_reason": result.skip_reason
            or ("no matching scope or owner capability" if result.skipped else None),
            "rate_limited": result.rate_limited,
            "configuration_missing": error_class == "configuration_missing",
            "error": result.error,
            "error_class": error_class,
        }
        run.request_count += requests_used
        if result.error:
            errors.append(f"{result.provider_id}: {result.error}")
        for discovery in result.discoveries[: int(plan["limits"]["max_results_per_query"])]:
            run.results_examined += 1
            provider_status["results_examined"] += 1
            created, prefiltered_or_qualified, candidate, prefilter_reason, evidence_deduped = _normalize_discovery(
                session, run, result.provider_id, discovery, brief, cf
            )
            if prefilter_reason:
                provider_status["prefiltered"] += 1
                prefilter_reasons = provider_status.setdefault("prefilter_reasons", {})
                prefilter_reasons[prefilter_reason] = prefilter_reasons.get(prefilter_reason, 0) + 1
                run.candidates_prefiltered += 1
                continue
            provider_status["accepted_discoveries"] += 1
            provider_status["evidence_deduped"] += evidence_deduped
            if created:
                run.candidates_created += 1
                provider_status["candidates_created"] += 1
            else:
                run.candidates_merged += 1
                provider_status["candidates_merged"] += 1
            if prefiltered_or_qualified:
                run.candidates_qualified += 1
                provider_status["qualified"] += 1
            elif candidate is not None and candidate_quality(candidate) in {"NEEDS_EVIDENCE", "NEEDS_ECONOMICS"}:
                provider_status["validation_pending"] += 1
                if candidate_quality(candidate) == "NEEDS_EVIDENCE":
                    provider_status["needs_evidence"] += 1
                else:
                    provider_status["needs_economics"] += 1
        statuses.append(provider_status)
    after = [row["id"] for row in top_moves(session)]
    run.top_moves_changed = before != after
    run.material_state_fingerprint = material_state_fingerprint(session, brief)
    run.provider_status_json = json.dumps(statuses)
    run.error_json = json.dumps(errors)
    run.completed_at = _now()
    run.updated_at = _now()
    # A provider can be intentionally skipped because its evidence is fresh or
    # its scope/capability route is inapplicable. Those are successful research
    # decisions, not failed runs. Only provider errors make a run partial/failed.
    successful = bool(statuses) and any(not row["error"] for row in statuses)
    run.status = "success" if successful and not errors else "partial" if successful else "failed"
    session.add(run)
    session.commit()
    session.refresh(run)
    return _run_payload(run)


def correct_soft_rejections(session: Session) -> dict[str, int]:
    """Reopen D021 discoveries terminalized only for missing validation facts."""
    provider_ids = {"fred_mortgage_opportunity", "treasury_yield_benchmark", "remote_ok_demand", "github_issue_demand"}
    changed = 0
    examined = 0
    for candidate in session.exec(select(OpportunityCandidate)).all():
        if candidate.source_type != "researched_external" or candidate.lifecycle_status != "rejected":
            continue
        evidence = session.exec(
            select(OpportunityEvidence).where(OpportunityEvidence.candidate_id == candidate.id)
        ).all()
        if not any(row.provider_id in provider_ids for row in evidence):
            continue
        examined += 1
        reasons = _json_list(candidate.rejection_reasons_json)
        if not reasons or not all(str(reason) in PENDING_QUALIFICATION_REASONS for reason in reasons):
            continue
        target = "researched" if candidate.verification_status == "verified" else "unverified"
        candidate.lifecycle_status = target
        candidate.qualification_status = "pending"
        candidate.safety_eligible = False
        candidate.rank_score = None
        candidate.updated_at = _now()
        session.add(candidate)
        session.add(
            OpportunityTransition(
                candidate_id=candidate.id,
                from_status="rejected",
                to_status=target,
                reason="d021r1_validation_state_correction",
                evidence_json=json.dumps(reasons),
            )
        )
        changed += 1
    session.commit()
    return {"examined": examined, "changed": changed}


def provider_yield_scorecard(session: Session) -> list[dict[str, Any]]:
    """Aggregate provider usefulness without retaining private candidate text."""
    provider_ids = [provider.provider_id for provider in default_discovery_providers()]
    totals: dict[str, dict[str, Any]] = {
        provider_id: {
            "provider_id": provider_id,
            "runs_attempted": 0,
            "requests": 0,
            "results_examined": 0,
            "prefiltered": 0,
            "unique_candidates_originated": 0,
            "candidates_corroborated": 0,
            "needs_evidence": 0,
            "needs_economics": 0,
            "decision_grade": 0,
            "actionable_accepted": 0,
            "completed_observed_outcomes": 0,
            "realized_value_outcomes": 0,
            "realized_value": 0.0,
        }
        for provider_id in provider_ids
    }
    canonical_run_ids: set[str] = set()
    for run in session.exec(select(OpportunityResearchRun)).all():
        try:
            run_plan = json.loads(run.plan_json or "{}")
        except json.JSONDecodeError:
            run_plan = {}
        try:
            statuses = json.loads(run.provider_status_json or "[]")
        except json.JSONDecodeError:
            statuses = []
        # D021R1 predates the explicit cohort marker, so its status schema is
        # the backward-compatible boundary for the derived canonical cohort.
        if run_plan.get("cohort") != "post_d021r1_canonical" and not any(
            "results_examined" in status for status in statuses
        ):
            continue
        canonical_run_ids.add(run.id)
        if not statuses:
            continue
        for status in statuses:
            provider_id = str(status.get("provider_id") or "")
            if provider_id not in totals:
                totals[provider_id] = {"provider_id": provider_id}
                for key in next(iter(totals.values())):
                    if key not in totals[provider_id]:
                        totals[provider_id][key] = 0 if key != "realized_value" else 0.0
            row = totals[provider_id]
            row["runs_attempted"] += int(bool(status.get("attempted", not status.get("skipped"))))
            row["requests"] += int(status.get("request_count") or 0)
            row["results_examined"] += int(status.get("results_examined", status.get("discovery_count", 0)) or 0)
            row["prefiltered"] += int(status.get("prefiltered", 0) or 0)
            # Candidate usefulness is derived below from durable evidence and
            # lifecycle state, so repeated observations do not inflate it.
    evidence_rows = session.exec(select(OpportunityEvidence)).all()
    candidates = {candidate.id: candidate for candidate in session.exec(select(OpportunityCandidate)).all()}
    by_candidate: dict[str, list[OpportunityEvidence]] = {}
    for evidence in evidence_rows:
        if evidence.research_run_id not in canonical_run_ids:
            continue
        if evidence.candidate_id:
            by_candidate.setdefault(evidence.candidate_id, []).append(evidence)
    observations = session.exec(select(OpportunityOutcomeObservation)).all()
    observations_by_candidate: dict[str, list[OpportunityOutcomeObservation]] = {}
    for observation in observations:
        observations_by_candidate.setdefault(observation.candidate_id, []).append(observation)
    for candidate_id, evidence in by_candidate.items():
        candidate = candidates.get(candidate_id)
        if candidate is None or not evidence:
            continue
        ordered = sorted(evidence, key=lambda row: (row.created_at, row.id))
        origin = ordered[0].provider_id
        if origin not in totals:
            continue
        totals[origin]["unique_candidates_originated"] += 1
        for provider_id in {row.provider_id for row in evidence if row.provider_id != origin}:
            if provider_id in totals:
                totals[provider_id]["candidates_corroborated"] += 1
        state = candidate_quality(candidate)
        if state == "NEEDS_EVIDENCE":
            totals[origin]["needs_evidence"] += 1
        elif state == "NEEDS_ECONOMICS":
            totals[origin]["needs_economics"] += 1
        elif state == "DECISION_GRADE":
            totals[origin]["decision_grade"] += 1
        if candidate.lifecycle_status in {"accepted", "attempted", "outcome_recorded"}:
            totals[origin]["actionable_accepted"] += 1
        for observation in observations_by_candidate.get(candidate_id, []):
            if observation.outcome_status == "completed":
                totals[origin]["completed_observed_outcomes"] += 1
                if observation.actual_net_value is not None:
                    totals[origin]["realized_value_outcomes"] += 1
                    totals[origin]["realized_value"] += float(observation.actual_net_value)
    for row in totals.values():
        row["realized_value"] = round(float(row["realized_value"]), 2)
        useful = int(row["unique_candidates_originated"] + row["candidates_corroborated"])
        requests = int(row["requests"])
        examined = int(row["results_examined"])
        originated = int(row["unique_candidates_originated"])
        row["request_to_useful_evidence_ratio"] = round(useful / requests, 3) if requests else 0.0
        row["examined_to_persisted_ratio"] = round(originated / examined, 3) if examined else 0.0
        row["persisted_to_decision_grade_ratio"] = (
            round(int(row["decision_grade"]) / originated, 3) if originated else 0.0
        )
        if row["completed_observed_outcomes"] or row["decision_grade"]:
            row["quality_classification"] = "HIGH_VALUE"
        elif useful:
            row["quality_classification"] = "USEFUL"
        elif row["runs_attempted"] >= 3:
            row["quality_classification"] = "LOW_YIELD"
        else:
            row["quality_classification"] = "INSUFFICIENT_OBSERVATION"
    return sorted(totals.values(), key=lambda row: row["provider_id"])


def mark_stale_evidence(session: Session, *, now: datetime | None = None) -> int:
    """Keep expired external evidence from retaining fresh qualification status."""
    ref = now or _now()
    changed: set[str] = set()
    for row in session.exec(select(OpportunityEvidence)).all():
        if row.status == "current" and row.expires_at and row.expires_at <= ref:
            row.status = "stale"
            row.updated_at = ref
            session.add(row)
            if row.candidate_id:
                changed.add(row.candidate_id)
    session.commit()
    for candidate_id in changed:
        candidate = session.get(OpportunityCandidate, candidate_id)
        if candidate:
            candidate.freshness_status = "stale"
            session.add(candidate)
            session.commit()
            qualify_candidate(session, candidate)
    from app.core.v2_validation import refresh_expired_validation_facts

    validation_changed = refresh_expired_validation_facts(session, now=ref)
    ranked_candidates(session)
    return len(changed) + validation_changed


def research_status(session: Session) -> dict[str, Any]:
    latest = list_research_runs(session, limit=1)
    scheduled = session.exec(select(ScheduledJob).where(ScheduledJob.job_type == "opportunity_refresh")).first()
    return {
        "latest_run": latest[0] if latest else None,
        "last_successful_refresh": next(
            (run for run in list_research_runs(session) if run["status"] in {"success", "partial"}), None
        ),
        "next_eligible_refresh": scheduled.next_run_at.isoformat() if scheduled and scheduled.next_run_at else None,
        "scheduler_job": scheduled.id if scheduled else None,
        "providers": [provider.provider_id for provider in default_discovery_providers()],
    }
