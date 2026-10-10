"""Canonical V2 intelligence-loop tests."""

from pathlib import Path
from unittest.mock import patch

from sqlmodel import select

from app.core.financial_snapshot import build_financial_snapshot
from app.core.opportunity_engine import detect_opportunities
from app.core.v2_intelligence import run_v2_intelligence_refresh
from app.db.models import CreditCard, OpportunityCandidate


def test_v2_refresh_preserves_deterministic_defensive_findings(sample_data):
    session = sample_data
    session.add(
        CreditCard(
            issuer="CardCo",
            card_name="Rewards",
            balance=3000,
            credit_limit=5000,
            apr=22,
            minimum_payment=90,
        )
    )
    session.commit()
    old_snapshot = build_financial_snapshot(session)
    old_findings = detect_opportunities(old_snapshot, session)
    assert old_findings, "fixture must exercise at least one defensive detector"

    with (
        patch(
            "app.core.v2_intelligence.run_opportunity_research",
            return_value={"status": "success", "request_count": 0},
        ),
    ):
        result = run_v2_intelligence_refresh(session, trigger="test")

    candidates = list(session.exec(select(OpportunityCandidate)).all())
    assert result["legacy_refresh_calls"] == 0
    assert result["legacy_recommendation_writes"] == 0
    assert result["defensive_findings"] == len(old_findings)
    assert {row.title for row in candidates} >= {row["title"] for row in old_findings}


def test_canonical_refresh_has_no_legacy_advice_import_or_write_dependency():
    source = Path("app/core/v2_intelligence.py").read_text(encoding="utf-8")
    for forbidden in (
        "run_refresh_cycle",
        "refresh_daily_opportunities",
        "persist_recommendations",
        "action_plan",
    ):
        assert forbidden not in source
