"""Offer intake validation and approval gate tests."""

from sqlmodel import select as sql_select

from app.core.financial_snapshot import build_financial_snapshot
from app.core.offer_intake import (
    INTAKE_PENDING,
    approve_offer,
    list_intake_offers,
    reject_offer,
    submit_offer,
    validate_offer_payload,
)
from app.core.opportunity_research import build_research_context_from_snapshot
from app.core.research_approval import get_research_offers_for_recommendations
from app.db.models import EventLogEntry, FinancialOffer
from app.events.types import EventType


def test_offer_without_source_rejected():
    errors = validate_offer_payload(
        {"provider": "Chase", "product_type": "balance_transfer", "apr": 0}
    )
    assert any("source_url" in e for e in errors)


def test_submit_offer_pending(session):
    offer = submit_offer(
        session,
        {
            "provider": "Chase",
            "product_type": "balance_transfer",
            "apr": 0,
            "balance_transfer_fee_percent": 3,
            "source_url": "https://example.com/offer",
            "offer_name": "0% 12mo BT",
        },
    )
    assert offer.intake_status == INTAKE_PENDING
    assert offer.intake_source == "user_intake"


def test_pending_offer_excluded_from_recommendations(session):
    submit_offer(
        session,
        {
            "provider": "Bank",
            "product_type": "hysa",
            "apy": 5.5,
            "source_url": "https://example.com/hysa",
        },
    )
    prelim = build_financial_snapshot(session)
    ctx = build_research_context_from_snapshot(prelim)
    offers, _ = get_research_offers_for_recommendations(session, ctx)
    assert not any(o.get("raw_source") == "approved_user_intake" for o in offers)


def test_approve_offer_emits_event(session):
    from app.events.bus import event_bus

    event_bus.set_session(session)
    offer = submit_offer(
        session,
        {
            "provider": "Ally",
            "product_type": "hysa",
            "apy": 4.5,
            "source_url": "https://ally.com/rates",
        },
    )
    approve_offer(session, offer.id)
    types = {e.event_type for e in session.exec(sql_select(EventLogEntry)).all()}
    assert EventType.OFFER_APPROVED.value in types


def test_reject_offer(session):
    offer = submit_offer(
        session,
        {
            "provider": "X",
            "product_type": "personal_loan",
            "apr": 9,
            "source_url": "https://example.com/pl",
        },
    )
    reject_offer(session, offer.id, reviewer_notes="terms unclear")
    updated = session.get(FinancialOffer, offer.id)
    assert updated.intake_status == "rejected"


def test_approved_offer_in_recommendations(session):
    offer = submit_offer(
        session,
        {
            "provider": "Ally",
            "product_type": "hysa",
            "apy": 5.0,
            "source_url": "https://ally.com/rates",
            "estimated_savings": 200,
        },
    )
    approve_offer(session, offer.id)
    prelim = build_financial_snapshot(session)
    ctx = build_research_context_from_snapshot(prelim)
    offers, _ = get_research_offers_for_recommendations(session, ctx)
    assert any(o.get("intake_offer_id") == offer.id for o in offers)


def test_list_intake_filters(session):
    submit_offer(
        session,
        {
            "provider": "A",
            "product_type": "cd",
            "apy": 4,
            "source_url": "https://a.com",
        },
    )
    pending = list_intake_offers(session, intake_status=INTAKE_PENDING)
    assert len(pending) >= 1
