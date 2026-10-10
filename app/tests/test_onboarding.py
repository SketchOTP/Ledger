"""Onboarding wizard tests."""

import json

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.onboarding import assess_onboarding, get_onboarding_state, is_onboarding_complete, start_onboarding
from app.db.models import Bill, BorrowerProfile, CreditCard, IncomeSource
from app.events.types import EventType
from app.main import app


@pytest.fixture
def client():
    from app.db.session import init_db

    init_db()
    return TestClient(app)


def test_empty_profile_low_completion(session, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "unknown_provider_xyz")
    monkeypatch.setattr(settings, "ai_base_url", "")
    result = assess_onboarding(session)
    assert result["completion_score"] < 70
    assert result["status"] in ("not_started", "incomplete")
    assert result["missing_required_fields"]


def test_income_and_bills_increase_completion(session):
    session.add(
        IncomeSource(
            source_name="Job",
            amount=2500,
            pay_frequency="biweekly",
            next_pay_date=__import__("datetime").date.today(),
        )
    )
    session.add(
        Bill(
            name="Rent",
            amount=1200,
            required=True,
            due_date=__import__("datetime").date.today(),
        )
    )
    session.commit()
    empty = assess_onboarding(session)
    session.add(
        BorrowerProfile(
            checking_balance=500,
            target_buffer=1000,
            credit_score=720,
        )
    )
    session.commit()
    fuller = assess_onboarding(session)
    assert fuller["completion_score"] > empty["completion_score"]


def test_missing_credit_card_apr_blocks_credit_optimization(session):
    session.add(
        CreditCard(
            issuer="Bank",
            card_name="Visa",
            balance=2000,
            credit_limit=5000,
            apr=0,
            minimum_payment=50,
        )
    )
    session.commit()
    result = assess_onboarding(session)
    assert result["optimization_readiness"]["credit"]["ready"] is False
    assert any("APR" in b for b in result["optimization_readiness"]["credit"]["blocking"])


def test_no_debts_skips_debts_section(session):
    result = assess_onboarding(session)
    debts = next(s for s in result["sections"] if s["id"] == "debts")
    assert debts["skipped"] is True
    assert debts["complete"] is True


def test_no_credit_cards_skips_credit_cards_section(session):
    result = assess_onboarding(session)
    cards_sec = next(s for s in result["sections"] if s["id"] == "credit_cards")
    assert cards_sec["skipped"] is True
    assert cards_sec["complete"] is True


def test_rent_housing_completes_mortgage_section(session):
    session.add(
        BorrowerProfile(
            housing_status="rent",
            monthly_rent=1500,
            rent_due_date=__import__("datetime").date.today(),
        )
    )
    session.commit()
    result = assess_onboarding(session)
    housing = next(s for s in result["sections"] if s["id"] == "mortgage")
    assert housing["complete"] is True


def test_no_mortgage_incomplete_until_account(session):
    session.add(BorrowerProfile(housing_status="mortgage"))
    session.commit()
    result = assess_onboarding(session)
    housing = next(s for s in result["sections"] if s["id"] == "mortgage")
    assert housing["complete"] is False


def test_ai_misconfigured_downgrades_ai_report_readiness(monkeypatch, session):
    monkeypatch.setattr(settings, "ai_provider", "not_a_real_provider")
    monkeypatch.setattr(settings, "ai_base_url", "")
    result = assess_onboarding(session)
    assert result["report_readiness"]["ai_enhanced"] is False
    ai_sec = next(s for s in result["sections"] if s["id"] == "ai_provider_check")
    assert ai_sec["complete"] is False


def test_mock_ai_provider_enables_ai_readiness(session, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "mock")
    result = assess_onboarding(session)
    assert result["report_readiness"]["ai_enhanced"] is True


def test_completion_includes_data_quality(session, sample_data):
    session.add(BorrowerProfile(checking_balance=1000, credit_score=700, target_buffer=500))
    session.commit()
    result = assess_onboarding(session)
    assert "overall_score" in result["data_quality_after_onboarding"]
    assert result["data_quality_after_onboarding"]["status"]


def test_onboarding_api_get_shape(client, monkeypatch):
    """GET status against file DB (POST covered by direct session tests)."""
    monkeypatch.setattr(settings, "ai_provider", "mock")
    res = client.get("/api/onboarding")
    assert res.status_code == 200
    data = res.json()
    assert "completion_score" in data
    assert "sections" in data


def test_onboarding_start_sets_started_at(session):
    start_onboarding(session)
    result = assess_onboarding(session)
    assert result["started_at"] is not None


def test_target_buffer_optional_when_unset(session):
    session.add(BorrowerProfile(checking_balance=500, target_buffer=0))
    session.commit()
    result = assess_onboarding(session)
    buf = next(s for s in result["sections"] if s["id"] == "target_buffer")
    assert buf["complete"] is True
    assert buf.get("skipped") is True
    assert "target_buffer" not in result["missing_required_fields"]


def test_completed_at_skips_login_wizard_even_if_score_below_100(session):
    """Returning users are not sent back through setup when new gates are added."""
    from datetime import UTC, datetime

    state = get_onboarding_state(session)
    state.completed_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(state)
    session.commit()
    assessment = assess_onboarding(session)
    assert assessment["completion_score"] < 100
    assert is_onboarding_complete(session) is True


def test_events_emitted(session):
    from sqlmodel import select

    from app.api.routes_onboarding import (
        onboarding_complete,
        onboarding_start,
        onboarding_step_completed,
    )
    from app.db.models import EventLogEntry
    from app.events.bus import event_bus

    event_bus.set_session(session)
    onboarding_start(session=session)
    onboarding_step_completed("income", session=session)
    onboarding_complete(session=session)
    types = {e.event_type for e in session.exec(select(EventLogEntry)).all()}
    assert EventType.ONBOARDING_STARTED.value in types
    assert EventType.ONBOARDING_STEP_COMPLETED.value in types
    assert EventType.ONBOARDING_COMPLETED.value in types


def test_declarations_patch(client, session):
    res = client.patch(
        "/api/onboarding/declarations",
        json={"no_mortgage": True, "no_credit_cards": True},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["declarations"]["no_mortgage"] is True


def test_settings_endpoint_shape(client, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "mock")
    res = client.get("/api/onboarding")
    assert res.status_code == 200
    data = res.json()
    assert "completion_score" in data
    assert "status" in data
    assert "sections" in data
    assert "report_readiness" in data
    assert "setup_roadmap" in data
    assert "confidence_mode" in data
    assert "api_key" not in json.dumps(data)


def test_good_enough_core_unlocks_dashboard(session, monkeypatch):
    """Income + cash + bills unlock the app without AI or 100% completion."""
    from datetime import date

    from app.core.onboarding import complete_onboarding

    monkeypatch.setattr(settings, "ai_provider", "unknown_provider_xyz")
    monkeypatch.setattr(settings, "ai_base_url", "")
    session.add(
        IncomeSource(
            source_name="Job",
            amount=2500,
            pay_frequency="biweekly",
            next_pay_date=date.today(),
        )
    )
    session.add(
        Bill(
            name="Rent",
            amount=1200,
            required=True,
            due_date=date.today(),
        )
    )
    session.add(BorrowerProfile(checking_balance=500))
    session.commit()
    assessment = assess_onboarding(session)
    assert assessment["good_enough_ready"] is True
    assert assessment["confidence_mode"] == "limited"
    finished = complete_onboarding(session)
    assert finished["wizard_complete"] is True
    assert is_onboarding_complete(session) is True
    assert finished["finished_with_limited_confidence"] is True
