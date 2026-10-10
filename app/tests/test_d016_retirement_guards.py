"""D016 guards for retired parallel intelligence surfaces."""

from pathlib import Path

from sqlmodel import select

from app.core.v2_opportunities import normalize_legacy_financial_offers
from app.db.models import FinancialOffer, OpportunityCandidate
from app.main import app


def test_legacy_offer_normalization_is_idempotent(session):
    offer = FinancialOffer(
        provider="Example Bank",
        product_type="balance_transfer",
        offer_name="Historical transfer terms",
        source_url="https://example.test/terms",
        intake_source="research",
        evidence_notes="issuer terms",
    )
    session.add(offer)
    session.commit()

    first = normalize_legacy_financial_offers(session)
    second = normalize_legacy_financial_offers(session)
    rows = list(session.exec(select(OpportunityCandidate)).all())

    assert first["created"] == 1
    assert second["created"] == 0
    assert second["already_migrated"] == 1
    assert len(rows) == 1
    assert rows[0].verification_status == "verified"
    assert rows[0].expected_net_low is None
    assert rows[0].expected_net_high is None


def test_retired_runtime_modules_are_not_present():
    for relative in (
        "app/core/simulation_engine.py",
        "app/api/routes_simulations.py",
        "app/api/routes_plugins.py",
        "app/plugins/registry.py",
        "app/research/researcher.py",
        "app/research/pipeline.py",
        "app/core/financial_memory.py",
    ):
        assert not Path(relative).exists(), relative


def test_retired_routers_and_startup_surfaces_are_absent():
    paths = set(app.openapi().get("paths", {}))
    assert not any(path.startswith("/api/offer-intake") for path in paths)
    assert not any(path.startswith("/api/plugins") for path in paths)
    assert not any(path.startswith("/api/simulations") for path in paths)
