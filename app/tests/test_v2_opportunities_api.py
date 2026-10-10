"""Real FastAPI boundary coverage for the additive D002 routes."""

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.deps import get_db
from app.db.models import BorrowerProfile
from app.main import app
from app.research.providers.base import (
    DiscoveryClaim,
    OpportunityDiscovery,
    OpportunityDiscoveryResult,
    ResearchProvider,
    ResearchProviderResult,
)


def test_v2_opportunity_api_contract():
    # TestClient serves routes from another thread; StaticPool keeps the real
    # FastAPI dependency on the same isolated in-memory SQLite database.
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    session.add(BorrowerProfile(checking_balance=10000, target_buffer=100))
    session.commit()
    app.dependency_overrides[get_db] = lambda: session
    client = TestClient(app)
    try:
        assert client.get("/api/v2/capability-profile").status_code == 200
        today = client.get("/api/v2/today")
        assert today.status_code == 200
        assert today.json()["safety_status"] in {"safe", "tight", "at_risk"}
        assert "financial_state" in today.json()
        assert client.put("/api/v2/capability-profile", json={"max_setup_hours": 4}).status_code == 200
        skill = client.post("/api/v2/capability-profile/skills", json={"name": "Writing"})
        assert skill.status_code == 200
        assert (
            client.put(f"/api/v2/capability-profile/skills/{skill.json()['id']}", json={"proficiency": 0.9}).status_code
            == 200
        )
        asset = client.post("/api/v2/capability-profile/assets", json={"name": "Template library"})
        assert asset.status_code == 200
        assert (
            client.put(
                f"/api/v2/capability-profile/assets/{asset.json()['id']}", json={"availability": "limited"}
            ).status_code
            == 200
        )
        assert client.post("/api/v2/candidates", json={}).status_code == 400
        created = client.post(
            "/api/v2/candidates",
            json={
                "title": "Publish template",
                "source_type": "owner_supplied",
                "expected_net_low": 500,
                "expected_net_high": 700,
                "probability_low": 0.5,
                "probability_high": 0.7,
                "scalability": 0.8,
                "repeatability": 0.8,
                "automation_potential": 0.8,
                "reversibility": 0.8,
            },
        )
        assert created.status_code == 200
        candidate_id = created.json()["id"]
        assert client.get("/api/v2/candidates").status_code == 200
        assert client.get(f"/api/v2/candidates/{candidate_id}").status_code == 200
        assert client.get("/api/v2/candidates/missing").status_code == 404
        assert client.post(f"/api/v2/candidates/{candidate_id}/transition/accepted").status_code == 400
        qualified = client.post(f"/api/v2/candidates/{candidate_id}/qualify")
        assert qualified.status_code == 200 and qualified.json()["eligible"] is True
        action = client.post(f"/api/v2/candidates/{candidate_id}/accept")
        assert action.status_code == 200 and action.json()["action"]["opportunity_candidate_id"] == candidate_id
        assert client.get("/api/v2/today").json()["active_candidate_actions"][0]["id"] == action.json()["action"]["id"]
        # A repeated owner click keeps one active action and its frozen prediction.
        repeated_action = client.post(f"/api/v2/candidates/{candidate_id}/accept").json()["action"]
        assert repeated_action["id"] == action.json()["action"]["id"]
        outcome = client.put(
            f"/api/v2/candidates/{candidate_id}/outcome",
            json={
                "financial_action_id": action.json()["action"]["id"],
                "outcome_status": "failed",
                "actual_net_value": 0,
            },
        )
        assert outcome.status_code == 200 and outcome.json()["actual_setup_hours"] is None
        assert client.get(f"/api/v2/candidates/{candidate_id}/outcome").status_code == 200
        assert client.get("/api/v2/learning").status_code == 200
        moves = client.get("/api/v2/moves")
        assert moves.status_code == 200 and "candidates" in moves.json()
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_v2_owner_dismissal_is_semantic_and_rejects_bad_codes():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    session.add(BorrowerProfile(checking_balance=10000, target_buffer=100))
    session.commit()
    app.dependency_overrides[get_db] = lambda: session
    client = TestClient(app)
    try:
        candidate = client.post(
            "/api/v2/candidates",
            json={
                "title": "A bounded owner experiment",
                "expected_net_low": 100,
                "expected_net_high": 200,
                "probability_low": 0.5,
                "probability_high": 0.6,
            },
        ).json()
        candidate_id = candidate["id"]
        assert client.post(f"/api/v2/candidates/{candidate_id}/qualify").json()["eligible"] is True
        dismissed = client.post(
            f"/api/v2/candidates/{candidate_id}/dismiss",
            json={"reason_code": "owner_timing", "note": "after a move"},
        )
        assert dismissed.status_code == 200 and dismissed.json()["lifecycle_status"] == "dismissed"
        invalid = client.post(f"/api/v2/candidates/{candidate_id}/dismiss", json={"reason_code": "nope"})
        assert invalid.status_code == 400
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_v2_research_api_contract(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    session.add(BorrowerProfile(checking_balance=10000, target_buffer=100))
    session.commit()

    class ReplayProvider(ResearchProvider):
        provider_id = "api-replay"

        def fetch(self, _context):
            return ResearchProviderResult(provider_id=self.provider_id, skipped=True)

        def discover(self, _context, _plan):
            return OpportunityDiscoveryResult(
                provider_id=self.provider_id,
                request_count=1,
                discoveries=[
                    OpportunityDiscovery(
                        title="API research candidate",
                        category="digital_product",
                        summary="Structured replay",
                        source_url="https://api.example.test/source",
                        source_title="API source",
                        source_domain="api.example.test",
                        publisher="API replay",
                        source_quality="official_primary",
                        external_identity="api:research-candidate",
                        claims=[
                            DiscoveryClaim(
                                claim_type="demand_signal",
                                value="observed",
                                source_quality="official_primary",
                                evidence_strength=0.9,
                            )
                        ],
                        candidate_fields={
                            "verification_status": "verified",
                            "freshness_status": "current",
                            "expected_net_low": 100,
                            "expected_net_high": 200,
                            "probability_low": 0.5,
                            "probability_high": 0.7,
                        },
                    )
                ],
            )

    monkeypatch.setattr("app.core.opportunity_research.default_discovery_providers", lambda: [ReplayProvider()])
    app.dependency_overrides[get_db] = lambda: session
    client = TestClient(app)
    try:
        assert client.post("/api/v2/research/refresh", json={"scope": "bogus"}).status_code == 400
        refresh = client.post("/api/v2/research/refresh", json={"scope": "all"})
        assert refresh.status_code == 200 and refresh.json()["candidates_created"] == 1
        assert client.get("/api/v2/research/runs").json()["runs"]
        assert client.get("/api/v2/research/status").status_code == 200
    finally:
        app.dependency_overrides.clear()
        session.close()
