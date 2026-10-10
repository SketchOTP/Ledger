"""Household CFO profile setup — checklist, feature gates, import templates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlmodel import Session

from app.core.onboarding import (
    NEXT_STEP_HINTS,
    SECTION_LABELS,
    assess_onboarding,
    get_onboarding_state,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE_DIR = REPO_ROOT / "data" / "import_templates"

FEATURE_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "cashflow_snapshot",
        "label": "Cashflow snapshot",
        "description": "Safe-to-spend and paycheck window on Home.",
        "requires_sections": ("income", "cash_on_hand", "bills"),
        "readiness_key": "deterministic",
        "fix_path": "/money",
        "fix_label": "My Money → Paycheck & Cash",
    },
    {
        "id": "ai_advice",
        "label": "AI-enhanced advice",
        "description": "Narrated recommendations using your configured AI provider.",
        "requires_sections": ("ai_provider_check",),
        "readiness_key": "ai_enhanced",
        "fix_path": "/ai-settings",
        "fix_label": "AI settings",
    },
    {
        "id": "market_rates",
        "label": "Market-backed rate comparisons",
        "description": "Mortgage, HYSA, and card benchmarks from approved research.",
        "requires_sections": ("mortgage", "credit_cards"),
        "readiness_key": "research_rates",
        "fix_path": "/research-admin",
        "fix_label": "Research admin",
    },
    {
        "id": "credit_optimization",
        "label": "Credit card optimization",
        "description": "Balance transfer and payoff comparisons with full card data.",
        "requires_sections": ("credit_cards",),
        "readiness_key": "credit_optimization",
        "fix_path": "/credit-cards",
        "fix_label": "Credit cards",
    },
    {
        "id": "mortgage_optimization",
        "label": "Mortgage / refinance analysis",
        "description": "Refinance scenarios vs approved market mortgage rate.",
        "requires_sections": ("mortgage",),
        "readiness_key": "mortgage_optimization",
        "fix_path": "/mortgage",
        "fix_label": "Housing",
    },
    {
        "id": "forecast_stress",
        "label": "90-day cash forecast",
        "description": "Stress forecast and lowest-cash-day warnings.",
        "requires_sections": ("income", "bills"),
        "readiness_key": "deterministic",
        "fix_path": "/forecast",
        "fix_label": "Forecast",
    },
)

IMPORT_TEMPLATES: tuple[dict[str, str], ...] = (
    {
        "id": "bills",
        "label": "Bills",
        "filename": "bills_template.csv",
        "description": "Monthly and one-off bills with due dates.",
        "import_path": "/money?tab=bills",
    },
    {
        "id": "debts",
        "label": "Debts",
        "filename": "debts_template.csv",
        "description": "Loans and lines of credit with APR and minimums.",
        "import_path": "/money?tab=debts",
    },
    {
        "id": "credit_cards",
        "label": "Credit cards",
        "filename": "credit_cards_template.csv",
        "description": "Card balances, limits, APR, and due dates.",
        "import_path": "/credit-cards",
    },
    {
        "id": "groceries",
        "label": "Legacy groceries",
        "filename": "groceries_template.csv",
        "description": "Historical grocery rows only; new V2 spending uses transactions.",
        "import_path": "/transactions",
    },
)


def _section_map(assessment: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["id"]: s for s in assessment.get("sections", [])}


def _feature_blockers(
    session: Session,
    assessment: dict[str, Any],
) -> list[dict[str, Any]]:
    sections = _section_map(assessment)
    readiness = assessment.get("report_readiness") or {}
    blockers: list[dict[str, Any]] = []

    for feat in FEATURE_CATALOG:
        missing_sections: list[str] = []
        for sid in feat.get("requires_sections") or ():
            sec = sections.get(sid)
            if not sec or (not sec.get("complete") and not sec.get("skipped")):
                missing_sections.append(SECTION_LABELS.get(sid, sid))

        rkey = feat.get("readiness_key")
        readiness_ok = True if rkey is None else bool(readiness.get(rkey))

        if not missing_sections and readiness_ok:
            continue

        reasons: list[str] = []
        if missing_sections:
            reasons.append(f"Complete: {', '.join(missing_sections)}")
        if not readiness_ok and rkey:
            reasons.append(f"Gate not met: {rkey.replace('_', ' ')}")

        blockers.append(
            {
                "feature_id": feat["id"],
                "feature_label": feat["label"],
                "section_id": missing_sections[0] if missing_sections else None,
                "section_label": missing_sections[0] if missing_sections else None,
                "reason": "; ".join(reasons) if reasons else "Additional setup required",
                "fix_path": feat["fix_path"],
                "fix_label": feat["fix_label"],
                "hint": NEXT_STEP_HINTS.get(
                    (feat.get("requires_sections") or ("",))[0], ""
                ),
                "summary": True,
            }
        )

    return blockers


def _checklist(assessment: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for sec in assessment.get("sections", []):
        sid = sec["id"]
        fix_path = {
            "income": "/money?tab=paycheck",
            "cash_on_hand": "/money?tab=cash",
            "bills": "/money?tab=bills",
            "debts": "/money?tab=debts",
            "mortgage": "/mortgage",
            "credit_cards": "/credit-cards",
            "credit_score": "/money?tab=profile",
            "target_buffer": "/money?tab=cash",
            "ai_provider_check": "/ai-settings",
        }.get(sid, "/onboarding")
        items.append(
            {
                "id": sid,
                "label": sec.get("label") or SECTION_LABELS.get(sid, sid),
                "complete": bool(sec.get("complete") or sec.get("skipped")),
                "skipped": bool(sec.get("skipped")),
                "score": sec.get("score"),
                "missing_fields": sec.get("missing_fields") or [],
                "blocking_issues": sec.get("blocking_issues") or [],
                "fix_path": fix_path,
                "wizard_path": "/onboarding",
            }
        )
    return items


def list_import_templates() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for meta in IMPORT_TEMPLATES:
        path = TEMPLATE_DIR / meta["filename"]
        out.append(
            {
                **meta,
                "download_path": f"/api/household-setup/templates/{meta['id']}",
                "exists": path.is_file(),
            }
        )
    return out


def template_file_path(template_id: str) -> Path | None:
    for meta in IMPORT_TEMPLATES:
        if meta["id"] == template_id:
            return TEMPLATE_DIR / meta["filename"]
    return None


def build_household_setup_status(session: Session) -> dict[str, Any]:
    """One-page household setup status with checklist and feature blockers."""
    assessment = assess_onboarding(session, emit_events=False)
    state = get_onboarding_state(session)
    blockers = _feature_blockers(session, assessment)
    features = []
    readiness = assessment.get("report_readiness") or {}
    sections = _section_map(assessment)

    for feat in FEATURE_CATALOG:
        missing_sections = [
            SECTION_LABELS.get(sid, sid)
            for sid in feat.get("requires_sections") or ()
            if not (sections.get(sid, {}).get("complete") or sections.get(sid, {}).get("skipped"))
        ]
        rkey = feat.get("readiness_key")
        readiness_ok = True if rkey is None else bool(readiness.get(rkey))
        features.append(
            {
                "id": feat["id"],
                "label": feat["label"],
                "description": feat["description"],
                "enabled": not missing_sections and readiness_ok,
                "missing_sections": missing_sections,
                "fix_path": feat["fix_path"],
                "fix_label": feat["fix_label"],
            }
        )

    setup_blocker_summary = [
        b for b in blockers if b.get("summary") or b.get("section_id")
    ]

    return {
        "completion_score": assessment["completion_score"],
        "status": assessment["status"],
        "wizard_complete": bool(state.completed_at),
        "started_at": assessment.get("started_at"),
        "completed_at": assessment.get("completed_at"),
        "next_best_step": assessment.get("next_best_step"),
        "checklist": _checklist(assessment),
        "features": features,
        "feature_blockers": blockers,
        "setup_blocker_summary": setup_blocker_summary[:12],
        "missing_required_fields": assessment.get("missing_required_fields") or [],
        "blocking_issues": assessment.get("blocking_issues") or [],
        "report_readiness": readiness,
        "import_templates": list_import_templates(),
        "guided_flow_path": "/household-setup",
        "onboarding_path": "/onboarding",
    }
