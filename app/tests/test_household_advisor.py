"""Focused tests for the household resilience and wealth-advisor layer."""

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.deps import get_db
from app.core.cashflow_engine import calculate_cashflow
from app.core.household_advisor import (
    build_advisory,
    build_resilience,
    build_wealth_plan,
)
from app.core.v2_opportunities import top_moves
from app.db.models import (
    Bill,
    BorrowerProfile,
    CapabilityAsset,
    CapabilityProfile,
    CapabilitySkill,
    HouseholdProfile,
    IncomeSource,
)
from app.main import app


def test_resilience_uses_stress_scenarios_without_probability_claims(session):
    session.add(BorrowerProfile(checking_balance=2_000, target_buffer=200))
    session.add(
        IncomeSource(
            source_name="Pay", amount=2_000, pay_frequency="monthly", next_pay_date=date.today() + timedelta(days=7)
        )
    )
    session.add(Bill(name="Rent", amount=900, due_date=date.today() + timedelta(days=14)))
    session.commit()
    result = build_resilience(session)
    assert result["target"]["recommended"] > 0
    assert {row["name"] for row in result["scenarios"]} >= {"income_loss_25", "income_loss_50", "income_loss_100"}
    assert all(row["probability"] is None for row in result["scenarios"])


def test_wealth_plan_reconciles_exactly_and_does_not_change_d001(session):
    session.add(BorrowerProfile(checking_balance=8_000, target_buffer=100))
    session.add(CapabilityProfile(max_preferred_capital=200, preferred_effort_level="moderate"))
    session.commit()
    cf = calculate_cashflow(session)
    before = float((cf.get("money_state") or {}).get("deployable_surplus") or 0)
    resilience = {"reserve_gap": 0.0}
    plan = build_wealth_plan(cf, resilience, HouseholdProfile(), session.get(CapabilityProfile, "default"))
    assert plan["allocation"]["reconciles"] is True
    assert sum(plan["allocation"][key] for key in ("save", "grow", "free")) == plan["free_cash"]
    assert float((calculate_cashflow(session).get("money_state") or {}).get("deployable_surplus") or 0) == before


def test_hypotheses_are_bounded_specific_and_unverified(session):
    session.add(CapabilityProfile(max_setup_hours=10, max_recurring_hours_per_week=4, max_preferred_capital=100))
    session.add(CapabilitySkill(name="3D modeling", category="design"))
    session.add(CapabilitySkill(name="Python automation", category="software"))
    session.add(CapabilityAsset(name="desktop printer", asset_type="equipment"))
    session.commit()
    advisory = build_advisory(session, generate_hypotheses=True)
    assert 1 <= len(advisory["hypotheses"]) <= 3
    assert all(row["status"] == "unverified" for row in advisory["hypotheses"])
    assert all(row["test_capital"] <= 35 for row in advisory["hypotheses"])
    assert all("demand" in " ".join(row["evidence_needed"]) for row in advisory["hypotheses"])
    assert top_moves(session) == []


def test_empty_capability_does_not_fabricate_growth_idea(session):
    advisory = build_advisory(session, generate_hypotheses=True)
    assert advisory["hypotheses"] == []
    assert advisory["top_wealth_action"]["kind"] in {"none", "free", "save"}


def test_advisory_routes_return_plain_safe_contract(session):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    route_session = Session(engine)
    app.dependency_overrides[get_db] = lambda: route_session
    client = TestClient(app)
    try:
        household = client.get("/api/v2/household")
        advisory = client.get("/api/v2/advisory")
        refreshed = client.post("/api/v2/advisory/refresh")
        assert household.status_code == 200, household.text
        assert advisory.status_code == 200, advisory.text
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["wealth_plan"]["allocation"]["reconciles"] is True
    finally:
        app.dependency_overrides.clear()
        route_session.close()
