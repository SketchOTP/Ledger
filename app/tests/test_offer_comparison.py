"""Offer comparison engine tests."""

import json

import pytest
from sqlmodel import Session, select

from app.core.data_quality import assess_data_quality
from app.core.offer_comparison import compare_offer
from app.core.offer_intake import submit_offer
from app.db.models import Bill, CreditCard, EventLogEntry, MortgageAccount
from app.events.types import EventType


def test_balance_transfer_compares_fee_vs_apr_savings(session: Session):
    session.add(
        CreditCard(
            issuer="Visa",
            card_name="Rewards",
            balance=5000,
            apr=22,
            minimum_payment=100,
            credit_limit=10000,
        )
    )
    session.commit()

    offer = submit_offer(
        session,
        {
            "provider": "Citi",
            "product_type": "balance_transfer",
            "apr": 0,
            "balance_transfer_fee_percent": 3,
            "source_url": "https://citi.com/bt",
        },
    )
    result = compare_offer(session, offer.id)
    assert result["verdict"] in ("compare", "strong_candidate", "monitor")
    assert result["estimated_total_delta"] > 0
    assert any("fee" in a.lower() for a in result["assumptions"])


def test_mortgage_refi_requires_break_even(session: Session):
    session.add(
        MortgageAccount(
            lender="Bank",
            original_balance=250000,
            current_balance=200000,
            interest_rate=7.5,
            monthly_payment=1400,
            escrow_amount=200,
            term_years=30,
            remaining_term_months=300,
        )
    )
    session.commit()

    offer = submit_offer(
        session,
        {
            "provider": "Lender",
            "product_type": "mortgage_refinance",
            "apr": 6.0,
            "closing_costs": 4000,
            "source_url": "https://lender.com/refi",
        },
    )
    result = compare_offer(session, offer.id)
    assert result["break_even_months"] is not None or result["verdict"] == "monitor"
    assert "break" in result["recommended_next_step"].lower() or result["break_even_months"]


def test_insurance_quote_compares_to_bill(session: Session):
    session.add(
        Bill(
            name="Auto Insurance",
            amount=180,
            category="insurance",
            required=True,
        )
    )
    session.commit()

    offer = submit_offer(
        session,
        {
            "provider": "Geico",
            "product_type": "insurance",
            "monthly_payment": 150,
            "source_url": "https://geico.com/quote",
        },
    )
    result = compare_offer(session, offer.id)
    assert result["estimated_monthly_delta"] == pytest.approx(30, abs=1)
    assert any("bill" in a.lower() for a in result["assumptions"])


def test_weak_data_quality_downgrades_verdict(session: Session):
    """Empty household — data quality blocking should not yield strong_candidate."""
    offer = submit_offer(
        session,
        {
            "provider": "X",
            "product_type": "hysa",
            "apy": 5,
            "estimated_savings": 1000,
            "source_url": "https://x.com",
        },
    )
    dq = assess_data_quality(session)
    result = compare_offer(session, offer.id)
    if dq.get("blocking"):
        assert result["verdict"] != "strong_candidate"


def test_compare_emits_event(session: Session):
    from app.events.bus import event_bus

    event_bus.set_session(session)
    session.add(
        CreditCard(
            issuer="A",
            card_name="Card",
            balance=1000,
            apr=20,
            minimum_payment=25,
            credit_limit=5000,
        )
    )
    session.commit()
    offer = submit_offer(
        session,
        {
            "provider": "B",
            "product_type": "balance_transfer",
            "apr": 0,
            "balance_transfer_fee_percent": 3,
            "source_url": "https://b.com",
        },
    )
    compare_offer(session, offer.id)
    types = {e.event_type for e in session.exec(select(EventLogEntry)).all()}
    assert EventType.OFFER_COMPARED.value in types

    from app.db.models import FinancialOffer

    refreshed = session.get(FinancialOffer, offer.id)
    assert json.loads(refreshed.comparison_json).get("verdict")
