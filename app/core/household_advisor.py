"""Household resilience and personal-wealth advisory composition.

This module is deliberately advisory.  D001 remains the sole authority for
money that is safe to use; this layer explains that state, models bounded
stress scenarios, and creates unverified growth hypotheses from facts the
owner has actually entered.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from app.core.cashflow_engine import calculate_cashflow
from app.core.v2_opportunities import ingest_candidate
from app.db.models import (
    BorrowerProfile,
    CapabilityAsset,
    CapabilityProfile,
    CapabilitySkill,
    HouseholdAdvisorySnapshot,
    HouseholdCoverage,
    HouseholdIncomeRisk,
    HouseholdLifeEvent,
    HouseholdMember,
    HouseholdProfile,
    HouseholdRisk,
    HouseholdVehicle,
    OpportunityCandidate,
    OpportunityEvidence,
)


def _profile(session: Session) -> HouseholdProfile:
    row = session.exec(select(HouseholdProfile)).first()
    if row:
        return row
    row = HouseholdProfile()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _capability(session: Session) -> CapabilityProfile:
    row = session.exec(select(CapabilityProfile)).first()
    if row:
        return row
    row = CapabilityProfile(id="default")
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _monthly_income(session: Session) -> float:
    from app.db.models import IncomeSource

    factors = {
        "weekly": 52 / 12,
        "biweekly": 26 / 12,
        "semimonthly": 2,
        "monthly": 1,
        "quarterly": 1 / 3,
        "annual": 1 / 12,
    }
    return round(
        sum(
            float(row.amount or 0) * factors.get(str(row.pay_frequency).lower(), 1)
            for row in session.exec(select(IncomeSource)).all()
        ),
        2,
    )


def _essential_monthly(cashflow: dict[str, Any]) -> float:
    required = float(cashflow.get("monthly_obligations_total") or 0)
    variable = float(cashflow.get("planned_variable_spending") or 0)
    return round(max(0.0, required + variable), 2)


def _dedicated_reserve(session: Session) -> float:
    """Return explicitly dedicated resilience money, never generic D001 headroom."""
    profile = session.exec(select(HouseholdProfile)).first()
    if profile and float(profile.dedicated_resilience_balance or 0) > 0:
        return round(float(profile.dedicated_resilience_balance), 2)
    borrower = session.exec(select(BorrowerProfile)).first()
    return round(max(0.0, float(borrower.emergency_fund_balance or 0)) if borrower else 0.0, 2)


def _scenario(
    name: str, monthly_burn: float, monthly_income: float, months: int, income_factor: float
) -> dict[str, Any]:
    income = round(monthly_income * income_factor * months, 2)
    need = round(monthly_burn * months, 2)
    return {
        "name": name,
        "months": months,
        "income_assumption": income_factor,
        "required_living_cost": need,
        "income_remaining": income,
        "shortfall": round(max(0.0, need - income), 2),
        "probability": None,
        "note": "Stress scenario, not a prediction.",
    }


def _coverage_applies(coverage: HouseholdCoverage, risk_type: str) -> bool:
    """Match protection to the exposure it can actually mitigate."""
    kind = (coverage.coverage_type or "").casefold()
    risk = (risk_type or "").casefold()
    groups = {
        "health": ("health", "medical", "care"),
        "care": ("health", "medical", "care"),
        "vehicle": ("auto", "vehicle", "car", "transport"),
        "transport": ("auto", "vehicle", "car", "transport"),
        "income": ("disability", "income", "unemployment"),
        "job": ("disability", "income", "unemployment"),
        "housing": ("home", "rent", "property", "housing"),
        "rent": ("home", "rent", "property", "housing"),
    }
    tokens = groups.get(risk, tuple(part.strip() for part in risk.replace("/", " ").split() if part.strip()) or (risk,))
    return any(token and (token in kind or token in risk and token in kind) for token in tokens)


def build_resilience(session: Session, cashflow: dict[str, Any] | None = None) -> dict[str, Any]:
    cf = cashflow or calculate_cashflow(session)
    profile = _profile(session)
    monthly_burn = _essential_monthly(cf)
    income = _monthly_income(session)
    current_liquidity = float((cf.get("money_state") or {}).get("liquid_resources") or cf.get("checking_balance") or 0)
    tolerance = (profile.risk_tolerance or "moderate").lower()
    default_months = {"conservative": 6.0, "moderate": 3.0, "growth": 2.0}.get(tolerance, 3.0)
    recommended_months = float(profile.target_reserve_months or default_months)
    minimum_months = max(1.0, min(recommended_months, 1.0))
    strong_months = max(recommended_months, 6.0)
    risks = [row for row in session.exec(select(HouseholdRisk)).all() if row.active]
    events = list(session.exec(select(HouseholdLifeEvent)).all())
    vehicles = list(session.exec(select(HouseholdVehicle)).all())
    members = [row for row in session.exec(select(HouseholdMember)).all() if row.active]
    income_risks = [row for row in session.exec(select(HouseholdIncomeRisk)).all() if row.active]
    coverages = list(session.exec(select(HouseholdCoverage)).all())
    concentration = None
    from app.db.models import IncomeSource

    incomes = [
        float(row.amount or 0)
        * {"weekly": 52 / 12, "biweekly": 26 / 12, "semimonthly": 2, "monthly": 1}.get(
            str(row.pay_frequency).lower(), 1
        )
        for row in session.exec(select(IncomeSource)).all()
    ]
    if income > 0 and incomes:
        concentration = round(max(incomes) / income, 4)
    scenarios = [
        _scenario("income_loss_25", monthly_burn, income, 3, 0.75),
        _scenario("income_loss_50", monthly_burn, income, 3, 0.50),
        _scenario("income_loss_100", monthly_burn, income, 3, 0.0),
    ]
    if concentration is not None and concentration > 0.75:
        scenarios.append(_scenario("concentrated_income", monthly_burn, income, 6, 0.50))
    # Owner-entered income risk takes precedence over generic stress cases.
    for risk_row in income_risks:
        share = max(0.0, min(1.0, float(risk_row.income_share or 0)))
        affected = max(0.0, min(1.0, float(risk_row.affected_percent or 0)))
        months = max(1, int(risk_row.recovery_duration_months or 3))
        source_share = share or (1.0 if len(incomes) <= 1 else 1.0 / len(incomes))
        loss = (
            income * source_share * (affected or (0.5 if risk_row.stability_band in {"unstable", "seasonal"} else 0.25))
        )
        offset = max(0.0, float(risk_row.severance_or_benefit_offset or 0))
        # Income-replacement coverage only offsets the linked income-loss
        # scenario, after its factual waiting period; it never mitigates other
        # domains such as health, transport, or legal costs.
        for coverage in coverages:
            if (
                _coverage_applies(coverage, "income")
                and coverage.status == "adequate"
                and coverage.income_replacement_percent
            ):
                waiting_months = max(0.0, float(coverage.waiting_period_days or 0) / 30.0)
                covered_months = max(0.0, months - waiting_months)
                offset += loss * covered_months * max(0.0, min(1.0, float(coverage.income_replacement_percent)))
        scenarios.append(
            {
                "name": "income_source_risk",
                "months": months,
                "income_assumption": None,
                "required_living_cost": round(monthly_burn * months, 2),
                "income_remaining": round(max(0.0, income * months - loss * months + offset), 2),
                "shortfall": round(max(0.0, loss * months - offset), 2),
                "probability": None,
                "note": "Owner-entered income exposure, not a prediction.",
            }
        )
    for vehicle in vehicles:
        if vehicle.required_for_income and not (
            vehicle.alternate_transport or vehicle.backup_vehicle or vehicle.warranty
        ):
            high = float(
                vehicle.repair_exposure_high
                or vehicle.replacement_value
                or vehicle.repair_exposure_low
                or vehicle.deductible
                or 0
            )
            scenarios.append(
                {
                    "name": "essential_vehicle_shock",
                    "months": 1,
                    "income_assumption": 1.0,
                    "required_living_cost": round(monthly_burn + high, 2),
                    "income_remaining": round(income, 2),
                    "shortfall": round(max(0.0, high - income + monthly_burn), 2),
                    "probability": None,
                    "note": "Vehicle dependency scenario, not a prediction.",
                }
            )
    for coverage in coverages:
        if coverage.status in {"none", "partial", "unknown"} and coverage.coverage_gap:
            scenarios.append(
                {
                    "name": "coverage_gap",
                    "months": 1,
                    "income_assumption": 1.0,
                    "required_living_cost": round(float(coverage.coverage_gap), 2),
                    "income_remaining": 0.0,
                    "shortfall": round(float(coverage.coverage_gap), 2),
                    "probability": None,
                    "note": "Owner-entered coverage exposure, not a prediction.",
                }
            )
    for member in members:
        if member.financially_dependent and member.recurring_care_cost:
            cost = float(member.recurring_care_cost)
            months = max(1, int(member.expected_income_interruption_months or 1))
            scenarios.append(
                {
                    "name": "dependent_care",
                    "months": months,
                    "income_assumption": 1.0,
                    "required_living_cost": round(cost * months, 2),
                    "income_remaining": 0.0,
                    "shortfall": round(cost * months, 2),
                    "probability": None,
                    "note": "Owner-entered care scenario, not a prediction.",
                }
            )
    for risk in risks:
        impact = float(risk.monthly_impact or risk.impact_high or risk.impact_low or 0)
        if impact:
            # Adequate coverage limits a covered shock to the known out-of-
            # pocket maximum; no coverage fact means no invented offset.
            for coverage in coverages:
                if (
                    _coverage_applies(coverage, risk.risk_type)
                    and coverage.status == "adequate"
                    and coverage.out_of_pocket_max is not None
                ):
                    impact = min(impact, max(0.0, float(coverage.out_of_pocket_max)))
            scenarios.append(
                {
                    "name": risk.risk_type,
                    "months": int(risk.duration_months or 1),
                    "income_assumption": 1.0,
                    "required_living_cost": round(impact * int(risk.duration_months or 1), 2),
                    "income_remaining": 0.0,
                    "shortfall": round(impact * int(risk.duration_months or 1), 2),
                    "probability": None,
                    "note": "Owner-entered stress scenario, not a prediction.",
                }
            )
    upcoming_events = [
        {
            "type": row.event_type,
            "date": row.event_date.isoformat() if row.event_date else None,
            "cost": row.estimated_cost,
            "certainty": row.certainty,
        }
        for row in events
    ]
    for row in events:
        low = float(row.estimated_cost_low if row.estimated_cost_low is not None else row.estimated_cost or 0)
        high = float(row.estimated_cost_high if row.estimated_cost_high is not None else row.estimated_cost or low)
        if high:
            scenarios.append(
                {
                    "name": "life_event",
                    "months": max(1, int(row.duration_months or 1)),
                    "income_assumption": 1.0,
                    "required_living_cost": round(high, 2),
                    "income_remaining": 0.0,
                    "shortfall": round(high, 2),
                    "probability": None,
                    "note": "Known household event, not a prediction.",
                }
            )
    baseline_min = monthly_burn * minimum_months
    baseline_rec = monthly_burn * recommended_months
    baseline_strong = monthly_burn * strong_months
    scenario_shortfalls = [float(item.get("shortfall") or 0) for item in scenarios]
    significant = max(scenario_shortfalls or [0.0])
    # A single risk can extend the recommendation; interacting risks are capped
    # rather than summed catastrophically.
    target_min = round(max(baseline_min, min(significant, monthly_burn * 2 + baseline_min)), 2)
    target_recommended = round(max(baseline_rec, min(significant, baseline_strong * 1.5)), 2)
    target_strong = round(max(baseline_strong, min(significant + baseline_rec, baseline_strong * 2.0)), 2)
    dedicated = _dedicated_reserve(session)
    missing_domains = []
    if not members:
        missing_domains.append("people/dependents")
    if not income_risks:
        missing_domains.append("income stability")
    if not vehicles:
        missing_domains.append("transportation")
    if not coverages:
        missing_domains.append("coverage")
    if not events:
        missing_domains.append("upcoming events")
    return {
        "monthly_essential_cost": monthly_burn,
        "monthly_income": income,
        "income_concentration": concentration,
        "current_accessible_liquidity": round(current_liquidity, 2),
        "target": {
            "minimum": target_min,
            "recommended": target_recommended,
            "strong": target_strong,
            "months": {"minimum": minimum_months, "recommended": recommended_months, "strong": strong_months},
        },
        "current_reserve": dedicated,
        "dedicated_resilience": dedicated,
        "reserve_gap": round(max(0.0, target_recommended - dedicated), 2),
        "scenarios": scenarios,
        "known_risks": [{"type": row.risk_type, "severity": row.severity} for row in risks],
        "upcoming_events": upcoming_events,
        "vehicle_count": len(vehicles),
        "confidence": "high" if not missing_domains else "low",
        "missing_domains": missing_domains,
        "drivers": sorted({str(row["name"]) for row in scenarios if float(row.get("shortfall") or 0) > 0}),
        "uncertainty": "Stress scenarios show what would happen; they do not assign fake probabilities.",
    }


def build_wealth_plan(
    cashflow: dict[str, Any],
    resilience: dict[str, Any],
    profile: HouseholdProfile,
    capability: CapabilityProfile,
    qualified_growth_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    money = cashflow.get("money_state") or {}
    free_cash = round(max(0.0, float(money.get("deployable_surplus") or 0)), 2)
    reserve_gap = max(0.0, float(resilience.get("reserve_gap") or 0))
    # Funding pace is intentionally bounded; only imminent/under-minimum
    # resilience consumes all available free cash.
    minimum_target = float((resilience.get("target") or {}).get("minimum") or 0)
    current = float(resilience.get("current_reserve") or 0)
    urgency = 1.0 if current < minimum_target or any("loss" in str(x) for x in resilience.get("drivers", [])) else 0.5
    save = min(free_cash, reserve_gap * urgency, free_cash * (1.0 if urgency >= 1 else 0.5))
    remaining = max(0.0, free_cash - save)
    risk = (profile.risk_tolerance or "moderate").lower()
    default_growth_share = {"conservative": 0.10, "moderate": 0.25, "growth": 0.35}.get(risk, 0.25)
    requested = capability.max_preferred_capital
    growth_share = float(
        profile.desired_growth_share if profile.desired_growth_share is not None else default_growth_share
    )
    growth_share = max(0.0, min(1.0, growth_share))
    candidates = qualified_growth_candidates or []
    paid = [row for row in candidates if float(row.get("required_capital") or 0) > 0]
    selected = max(
        candidates,
        key=lambda row: (
            float(row.get("expected_net") or row.get("expected_net_high") or 0),
            -float(row.get("required_capital") or 0),
        ),
        default=None,
    )
    candidate_cap = float(selected.get("required_capital") or 0) if selected else 0.0
    experimental_capital = max(0.0, float((money.get("experimental_capital") or 0)))
    grow = min(
        remaining,
        remaining * growth_share,
        candidate_cap,
        experimental_capital,
        float(requested) if requested is not None else remaining,
    )
    if profile.desired_free_share is not None:
        free_floor = max(0.0, min(1.0, float(profile.desired_free_share))) * free_cash
        grow = min(grow, max(0.0, remaining - free_floor))
    free = max(0.0, remaining - grow)
    allocation = {"save": round(save, 2), "grow": round(grow, 2), "free": round(free, 2)}
    allocation["free_cash"] = free_cash
    allocation["reconciles"] = round(sum(allocation[key] for key in ("save", "grow", "free")), 2) == free_cash
    return {
        "free_cash": free_cash,
        "allocation": allocation,
        "why": (
            "Save follows the urgency of your safety gap; Grow is funded only for "
            "a qualified experiment and stays within its test cap; Free stays "
            "available for ordinary surprises and choices."
        ),
        "automatic_transfer": False,
        "d001_unchanged": True,
        "qualified_growth_candidate": bool(paid),
        "selected_growth_candidate_id": selected.get("candidate_id") if selected else None,
        "selected_growth_test_capital": round(candidate_cap, 2),
        "growth_share_ceiling": round(growth_share, 4),
        "experimental_capital": experimental_capital,
    }


def _hypothesis_payload(spec: dict[str, Any], capability: CapabilityProfile) -> dict[str, Any]:
    title = str(spec.get("title") or "").strip()
    buyer = str(spec.get("target_buyer") or "a narrowly defined buyer group").strip()
    problem = str(spec.get("problem") or "a costly repetitive problem").strip()
    skills = [str(item).strip() for item in spec.get("skills", []) if str(item).strip()]
    assets = [str(item).strip() for item in spec.get("assets", []) if str(item).strip()]
    if not title or not skills:
        return {}
    test_capital = max(0.0, float(spec.get("test_capital") or 0))
    max_hours = max(0.0, min(float(capability.max_setup_hours or 4), float(spec.get("setup_hours") or 4)))
    return {
        "title": title,
        "summary": (f"Test {title.lower()} for {buyer}. This is a hypothesis, not a forecast."),
        "category": "capability_matched_growth",
        "source_type": "ai_hypothesis",
        "external_identity": f"household-hypothesis:{title.lower()}:{buyer.lower()}",
        "verification_status": "unverified",
        "freshness_status": "unknown",
        "required_capital": test_capital,
        "capital_at_risk": test_capital,
        "setup_hours": max_hours,
        "recurring_hours_per_week": min(float(capability.max_recurring_hours_per_week or 2), 2.0),
        "time_to_value_days": capability.desired_time_to_value_days or 30,
        "matched_skills_json": json.dumps(skills),
        "matched_assets_json": json.dumps(assets),
        "required_activities_json": json.dumps(
            [] if capability.customer_interaction_willingness is False else ["customer_interaction"]
        ),
        "evidence": [{"status": "unverified", "needs": ["buyer demand", "comparable price", "paid-pilot response"]}],
        "assumptions": [
            "A buyer has the stated problem",
            "The owner can demonstrate the capability without buying new equipment",
        ],
        "required_activities": [] if capability.customer_interaction_willingness is False else ["customer_interaction"],
        "validation_experiment": {
            "hypothesis": str(spec.get("hypothesis") or f"A qualified buyer will pay for {title}."),
            "target_buyer": buyer,
            "problem": problem,
            "capability": ", ".join(skills),
            "reusable_asset": ", ".join(assets) if assets else "none",
            "demand_evidence": "Not yet established; verify with a bounded buyer test.",
            "monetization_model": "small paid pilot, then repeatable fixed-scope offer",
            "downside": "limited owner time and the stated test cap; no inventory or long lockup",
            "reversibility": "stop after the test without committing beyond the cap",
            "scale_condition": "continue only after paid demand and a repeatable delivery path are evidenced",
            "max_capital": round(test_capital, 2),
            "max_hours": max_hours,
            "success_metric": "one qualified buyer requests a paid pilot or preorder",
            "failure_metric": "no qualified buyer response after a bounded test",
            "review_date": (date.today() + timedelta(days=14)).isoformat(),
            "stop_loss": "stop at the stated test capital and effort cap",
            "next_decision": str(
                spec.get("next_step")
                or "continue only if demand is evidenced; otherwise stop and preserve remaining cash"
            ),
        },
    }


def generate_household_growth_hypotheses(
    session: Session, wealth_plan: dict[str, Any], *, limit: int = 3
) -> list[dict[str, Any]]:
    capability = _capability(session)
    skills = [
        row for row in session.exec(select(CapabilitySkill)).all() if row.monetization_willingness and row.name.strip()
    ]
    assets = [
        row for row in session.exec(select(CapabilityAsset)).all() if row.monetization_willingness and row.name.strip()
    ]
    if not skills:
        return []
    free_cash = float(wealth_plan.get("free_cash") or (wealth_plan.get("allocation") or {}).get("free_cash") or 0)
    money = wealth_plan.get("money_state") or {}
    experimental = float(money.get("experimental_capital") or 0)
    requested = float(capability.max_preferred_capital) if capability.max_preferred_capital is not None else free_cash
    budget = min(free_cash, experimental, requested, 35.0)
    specs: list[dict[str, Any]] = []
    # Prefer a configured AI synthesis. Providers that cannot satisfy the
    # bounded schema are treated as unavailable; deterministic fallback only
    # combines multiple entered capabilities/assets into a concrete test.
    try:
        from app.ai.providers import get_provider

        result = get_provider().generate_structured(
            "Return at most three concrete household growth hypotheses. "
            "Never invent demand, revenue, skills, or assets.",
            json.dumps(
                {
                    "skills": [row.name for row in skills[:8]],
                    "assets": [row.name for row in assets[:8]],
                    "max_test_capital": min(budget, 35.0),
                    "constraints": capability.model_dump(),
                },
                default=str,
            ),
            {"type": "object", "properties": {"hypotheses": {"type": "array"}}, "required": ["hypotheses"]},
        )
        specs = [item for item in result.get("hypotheses", []) if isinstance(item, dict)][:limit]
        specs = [item for item in specs if item.get("title") and item.get("skills")]
    except Exception:
        specs = []
    if not specs and len(skills) >= 2:
        combined = " + ".join(row.name.strip() for row in skills[:2])
        asset_names = [row.name.strip() for row in assets[:2]]
        specs = [
            {
                "title": f"Test a {combined} service for a specific workflow",
                "target_buyer": "local teams with that workflow",
                "problem": f"Reduce time spent on a repeatable workflow using {combined}.",
                "skills": [row.name for row in skills[:2]],
                "assets": asset_names,
                "test_capital": min(budget, 25.0),
                "setup_hours": 4,
                "next_step": "Offer one fixed-scope pilot and stop if no qualified buyer responds.",
            }
        ]
    rows: list[dict[str, Any]] = []
    for spec in specs[:limit]:
        payload = _hypothesis_payload(spec, capability)
        if not payload:
            continue
        row = ingest_candidate(session, payload)
        # AI hypotheses are intentionally not upgraded by ingest; keep their
        # test-capital truth synchronized on refresh for stable identities.
        row.required_capital = float(payload["required_capital"])
        row.capital_at_risk = float(payload["capital_at_risk"])
        row.experiment_json = json.dumps(payload["validation_experiment"], sort_keys=True)
        session.add(row)
        session.commit()
        experiment = payload["validation_experiment"]
        rows.append(
            {
                "id": row.id,
                "title": row.title,
                "summary": row.summary,
                "status": "unverified",
                "evidence_needed": ["buyer demand", "comparable price", "paid-pilot response"],
                "target_buyer": experiment.get("target_buyer"),
                "problem": experiment.get("problem"),
                "test_capital": float(experiment.get("max_capital") or 0),
                "capital_at_risk": float(row.capital_at_risk or 0),
                "required_capital": float(row.required_capital or 0),
                "source_type": row.source_type,
                "candidate_id": row.id,
            }
        )
    return rows


def synthesize_risk_ai(session: Session, resilience: dict[str, Any]) -> dict[str, Any]:
    """Optional privacy-bounded synthesis; dollars remain deterministic."""
    payload = {
        "income_concentration": resilience.get("income_concentration"),
        "monthly_income": resilience.get("monthly_income"),
        "monthly_essential_cost": resilience.get("monthly_essential_cost"),
        "known_risks": resilience.get("known_risks", []),
        "scenario_names": [row.get("name") for row in resilience.get("scenarios", [])],
    }
    schema = {
        "type": "object",
        "properties": {
            key: {"type": "array"}
            for key in (
                "main_risks",
                "strengths",
                "interactions",
                "missing_facts",
                "recommended_scenarios",
                "mitigations",
                "wealth_implications",
            )
        },
        "required": ["main_risks", "strengths"],
    }
    try:
        from app.ai.providers import get_provider

        result = get_provider().generate_structured(
            "Summarize coarse household financial risks. Do not output reserve "
            "dollars, probabilities, names, diagnoses, or legal narratives.",
            json.dumps(payload, sort_keys=True),
            schema,
        )
        if not isinstance(result, dict) or not isinstance(result.get("main_risks"), list):
            raise ValueError("unsupported AI risk summary")
        return {key: result.get(key, []) for key in schema["properties"]} | {"status": "available"}
    except Exception:
        return {key: [] for key in schema["properties"]} | {"status": "unavailable"}


def collect_hypothesis_evidence(session: Session, hypotheses: list[dict[str, Any]]) -> dict[str, Any]:
    """Attach only evidence explicitly linked to each hypothesis and claim."""
    examined = 0
    acquired = 0
    unresolved = 0
    per_hypothesis: list[dict[str, Any]] = []
    evidence_rows = session.exec(select(OpportunityEvidence).where(OpportunityEvidence.status == "current")).all()
    for item in hypotheses[:3]:
        candidate = session.get(OpportunityCandidate, item.get("candidate_id"))
        if candidate is None:
            continue
        evidence = json.loads(candidate.evidence_json or "[]")
        required_claims = {"buyer_demand", "comparable_price", "required_cost"}
        matches = [
            row for row in evidence_rows if row.candidate_id == candidate.id and row.claim_type in required_claims
        ]
        seen_claims = {row.claim_type for row in matches}
        if matches:
            for match in matches[:3]:
                marker = {
                    "status": "observed",
                    "claim_type": match.claim_type,
                    "provider_id": match.provider_id,
                    "source_url": match.source_url,
                    "evidence_id": match.id,
                }
                if marker not in evidence:
                    evidence.append(marker)
                    acquired += 1
        for claim_type in sorted(required_claims - seen_claims):
            marker = {
                "status": "needed",
                "claim_type": claim_type,
                "source": "hypothesis_validation",
                "candidate_id": candidate.id,
                "question": {
                    "buyer_demand": "Are the stated target buyers actively seeking this outcome?",
                    "comparable_price": "What do comparable offers currently charge?",
                    "required_cost": "What materials, tools, and marketplace fees are required for the smallest test?",
                }[claim_type],
            }
            if marker not in evidence:
                evidence.append(marker)
        if not required_claims.issubset(seen_claims):
            unresolved += 1
        candidate.evidence_json = json.dumps(evidence, sort_keys=True)
        session.add(candidate)
        examined += 1
        per_hypothesis.append(
            {
                "candidate_id": candidate.id,
                "claims_observed": sorted(seen_claims),
                "claims_needed": sorted(required_claims - seen_claims),
                "resolved": required_claims.issubset(seen_claims),
            }
        )
    session.commit()
    return {
        "hypotheses_examined": examined,
        "evidence_acquired": acquired,
        "evidence_gaps": unresolved,
        "hypotheses": per_hypothesis,
        "status": "bounded",
    }


def build_advisory(
    session: Session,
    *,
    generate_hypotheses: bool = False,
    hypotheses_override: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cashflow = calculate_cashflow(session)
    household = _profile(session)
    capability = _capability(session)
    resilience = build_resilience(session, cashflow)
    provisional = dict(build_wealth_plan(cashflow, resilience, household, capability))
    provisional["money_state"] = cashflow.get("money_state") or {}
    hypotheses = hypotheses_override if hypotheses_override is not None else (
        generate_household_growth_hypotheses(session, provisional) if generate_hypotheses else []
    )
    qualified_growth = []
    for item in hypotheses:
        row = session.get(OpportunityCandidate, item.get("candidate_id"))
        if row is not None and row.qualification_status == "qualified":
            qualified_growth.append(item)
    wealth = build_wealth_plan(cashflow, resilience, household, capability, qualified_growth)
    return {
        "generated_at": date.today().isoformat(),
        "resilience": resilience,
        "wealth_plan": wealth,
        "hypotheses": hypotheses,
        "top_wealth_action": _top_action(wealth, resilience, hypotheses),
        "risk_synthesis": synthesize_risk_ai(session, resilience) if generate_hypotheses else {"status": "not_run"},
        "privacy": {"used_member_identifiers": False, "used_sensitive_narrative": False},
    }


def _top_action(wealth: dict[str, Any], resilience: dict[str, Any], hypotheses: list[dict[str, Any]]) -> dict[str, Any]:
    allocation = wealth.get("allocation") or {}
    urgent = float(resilience.get("current_reserve") or 0) < float((resilience.get("target") or {}).get("minimum") or 0)
    if urgent and float(allocation.get("save") or 0) > 0:
        return {
            "kind": "save",
            "title": f"Set aside ${float(allocation['save']):,.0f} toward your safety reserve",
            "capital": allocation["save"],
        }
    selected_id = wealth.get("selected_growth_candidate_id")
    selected = next((item for item in hypotheses if item.get("candidate_id") == selected_id), None)
    if selected:
        return {
            "kind": "validate",
            "title": "Test one household-specific idea without committing much money",
            "capital": min(float(selected.get("test_capital") or 0), float(allocation.get("grow") or 0)),
        }
    if hypotheses and float(hypotheses[0].get("test_capital") or 0) == 0:
        return {"kind": "validate", "title": "Run a no-cost test before spending money", "capital": 0.0}
    if float(allocation.get("save") or 0) > 0:
        return {
            "kind": "save",
            "title": f"Set aside ${float(allocation['save']):,.0f} toward your safety reserve",
            "capital": allocation["save"],
        }
    if float(allocation.get("free") or 0) > 0:
        return {
            "kind": "free",
            "title": "Keep your remaining safe-to-use money available",
            "capital": allocation["free"],
        }
    return {"kind": "none", "title": "No extra wealth action is supported yet", "capital": 0.0}


def persist_advisory(session: Session, advisory: dict[str, Any]) -> HouseholdAdvisorySnapshot:
    row = session.exec(
        select(HouseholdAdvisorySnapshot).where(HouseholdAdvisorySnapshot.snapshot_key == "current")
    ).first()
    if row is None:
        row = HouseholdAdvisorySnapshot(snapshot_key="current")
    row.resilience_json = json.dumps(advisory["resilience"], sort_keys=True)
    row.wealth_plan_json = json.dumps(advisory["wealth_plan"], sort_keys=True)
    row.hypotheses_json = json.dumps(advisory["hypotheses"], sort_keys=True)
    row.provenance_json = json.dumps(
        {
            "d001_authoritative": True,
            "generated": "deterministic",
            "risk_synthesis": advisory.get("risk_synthesis", {"status": "not_run"}),
        },
        sort_keys=True,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
