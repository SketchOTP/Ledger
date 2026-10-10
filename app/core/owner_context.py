"""Presentation-neutral local owner context for V2 capability decisions.

Behavioral sidecars remain user-authored compatibility data.  This adapter keeps
them separate from the canonical D002 capability profile and exposes only an
explicitly reduced view to any future provider-facing integration.
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session, select

from app.core.behavioral_store import load_human_capital_records, load_life_goals, load_risk_checklist
from app.db.models import CapabilityAsset, CapabilityProfile, CapabilitySkill

_PROFILE_FIELDS = (
    "max_setup_hours",
    "max_recurring_hours_per_week",
    "preferred_effort_level",
    "customer_interaction_willingness",
    "sales_outreach_willingness",
    "physical_labor_willingness",
    "prefers_async",
    "prefers_automated",
    "prefers_low_touch",
    "prefers_recurring_income",
    "prefers_scalable_income",
    "allows_one_time_income",
    "desired_time_to_value_days",
    "minimum_meaningful_payoff",
    "max_preferred_capital",
)


def _refused_activities(profile: CapabilityProfile | None) -> list[str]:
    if profile is None:
        return []
    try:
        raw = json.loads(profile.refused_activities_json or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item).strip() for item in raw if str(item).strip()] if isinstance(raw, list) else []


def build_owner_context(session: Session, *, user_id: str) -> dict[str, Any]:
    """Combine canonical capability state with preserved local owner signals."""
    profile = session.get(CapabilityProfile, "default")
    profile_payload = {field: getattr(profile, field) if profile else None for field in _PROFILE_FIELDS}
    profile_payload.update(
        {
            "refused_activities": _refused_activities(profile),
            "notes": profile.notes if profile else "",
        }
    )
    skills = [
        {
            "name": row.name,
            "category": row.category,
            "proficiency": row.proficiency,
            "monetization_willingness": row.monetization_willingness,
        }
        for row in session.exec(select(CapabilitySkill)).all()
    ]
    assets = [
        {
            "name": row.name,
            "asset_type": row.asset_type,
            "availability": row.availability,
            "monetization_willingness": row.monetization_willingness,
        }
        for row in session.exec(select(CapabilityAsset)).all()
    ]
    return {
        "capability_profile": profile_payload,
        "skills": skills,
        "assets": assets,
        "priorities": load_life_goals(user_id),
        "protection_metadata": load_risk_checklist(user_id),
        "human_capital": load_human_capital_records(user_id),
    }


def build_provider_owner_context(owner_context: dict[str, Any]) -> dict[str, Any]:
    """Return the only owner context safe to pass to an external provider.

    Behavioral priorities, checklist values, human-capital descriptions, and
    notes are deliberately excluded.  D002 capability fields remain the sole
    provider-facing personalization contract.
    """
    profile = owner_context.get("capability_profile") or {}
    return {
        "capability": {
            "skills": [
                {"name": row.get("name", ""), "category": row.get("category", "")}
                for row in owner_context.get("skills") or []
                if row.get("monetization_willingness") and row.get("name")
            ],
            "assets": [
                {"name": row.get("name", ""), "asset_type": row.get("asset_type", "")}
                for row in owner_context.get("assets") or []
                if row.get("monetization_willingness")
                and row.get("availability") == "available"
                and row.get("name")
            ],
            "setup_effort": profile.get("max_setup_hours"),
            "recurring_effort": profile.get("max_recurring_hours_per_week"),
            "preferred_effort_level": profile.get("preferred_effort_level") or "unspecified",
            "customer_interaction_willingness": profile.get("customer_interaction_willingness"),
            "sales_outreach_willingness": profile.get("sales_outreach_willingness"),
            "physical_labor_willingness": profile.get("physical_labor_willingness"),
            "allows_one_time_income": bool(profile.get("allows_one_time_income", True)),
            "desired_time_to_value_days": profile.get("desired_time_to_value_days"),
            "minimum_meaningful_payoff_band": _coarse_band(profile.get("minimum_meaningful_payoff")),
            "max_preferred_capital_band": _coarse_band(profile.get("max_preferred_capital")),
            "prefers_automated": bool(profile.get("prefers_automated")),
            "prefers_async": bool(profile.get("prefers_async")),
            "prefers_low_touch": bool(profile.get("prefers_low_touch")),
            "prefers_recurring_income": bool(profile.get("prefers_recurring_income")),
            "prefers_scalable_income": bool(profile.get("prefers_scalable_income")),
            "refused_activity_count": len(_refused_activities_from_payload(profile)),
        },
        "personalization_limited": not bool(owner_context.get("skills") or owner_context.get("assets")),
    }


def _coarse_band(value: Any) -> str:
    try:
        amount = max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return "none"
    if amount == 0:
        return "none"
    if amount < 250:
        return "under_250"
    if amount < 1_000:
        return "250_to_999"
    if amount < 5_000:
        return "1000_to_4999"
    return "5000_or_more"


def _refused_activities_from_payload(profile: dict[str, Any]) -> list[str]:
    values = profile.get("refused_activities") or []
    return [str(item).strip() for item in values if str(item).strip()] if isinstance(values, list) else []
