"""Household financial health score."""

from typing import Any

from sqlmodel import Session

from app.core.cashflow_engine import calculate_cashflow
from app.core.dti_engine import calculate_dti
from app.core.risk_engine import scan_risks


def calculate_health_score(session: Session) -> dict[str, Any]:
    cf = calculate_cashflow(session)
    dti = calculate_dti(session)
    risks = scan_risks(session, cf)

    score = 100
    if cf["risk_level"] == "danger":
        score -= 40
    elif cf["risk_level"] == "tight":
        score -= 20
    if dti["status"] == "high-risk":
        score -= 25
    elif dti["status"] == "constrained":
        score -= 15
    score -= min(30, len(risks) * 5)
    score = max(0, min(100, score))

    return {
        "score": score,
        "cashflow_status": cf["risk_level"],
        "dti_status": dti["status"],
        "risk_count": len(risks),
        "safe_to_spend": cf["safe_to_spend"],
        "label": "excellent" if score >= 80 else "good" if score >= 60 else "fair" if score >= 40 else "critical",
    }
