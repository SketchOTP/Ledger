"""Evidence extraction tests — draft-only until approved."""

import json

from sqlmodel import select

from app.core.evidence import store_evidence
from app.core.evidence_extraction import (
    approve_extraction,
    parse_credit_card_offer,
    parse_grocery_receipt,
    parse_loan_quote,
    run_extraction,
)
from app.core.financial_snapshot import build_financial_snapshot
from app.core.offer_intake import INTAKE_PENDING, submit_offer
from app.core.opportunity_research import build_research_context_from_snapshot
from app.core.research_approval import get_research_offers_for_recommendations
from app.db.models import FinancialOffer, GroceryItem, GroceryPriceObservation, GroceryStore, Recommendation


def _store(session, tmp_path, monkeypatch, filename, data, evidence_type, **kwargs):
    from app.core import evidence as ev_mod

    monkeypatch.setattr(ev_mod, "EVIDENCE_DIR", tmp_path / "evidence")
    return store_evidence(
        session,
        filename=filename,
        content_type="text/plain",
        data=data,
        evidence_type=evidence_type,
        **kwargs,
    )


def test_txt_receipt_extracts_item_totals():
    text = b"Store: Aldi\nDate: 2026-05-20\nMilk 3.49\nEggs 2.99\nTotal: 6.48\n"
    parsed = parse_grocery_receipt(text.decode())
    assert parsed["fields"].get("store") == "Aldi"
    assert parsed["fields"].get("total") == 6.48
    assert len(parsed["line_items"]) == 2


def test_csv_receipt_extracts_line_items():
    text = b"item,price\nMilk,3.49\nEggs,2.99\n"
    parsed = parse_grocery_receipt(text.decode(), filename="receipt.csv")
    assert len(parsed["line_items"]) == 2
    assert parsed["line_items"][0]["name"] == "Milk"


def test_approved_grocery_receipt_is_read_only_after_product_retirement(
    session, tmp_path, monkeypatch
):
    text = b"Store: Aldi\nDate: 2026-05-20\nMilk 3.49\nBread 2.50\n"
    ev = _store(session, tmp_path, monkeypatch, "r.txt", text, "grocery_receipt")
    payload = run_extraction(session, ev.id)

    from app.core.evidence_extraction import approve_extraction, get_extraction

    record = get_extraction(session, payload["id"])
    assert record is not None
    assert approve_extraction(session, record.id).status == "approved"
    assert session.exec(select(GroceryItem)).all() == []
    assert session.exec(select(GroceryPriceObservation)).all() == []
    assert session.exec(select(GroceryStore)).all() == []


def test_loan_quote_extracts_apr_payment_term():
    text = """APR: 6.5%
Term: 60 months
Payment: $450.00
Origination fee: $500
"""
    parsed = parse_loan_quote(text)
    assert parsed["fields"]["apr"] == 6.5
    assert parsed["fields"]["term_months"] == 60
    assert parsed["fields"]["payment"] == 450.0
    assert parsed["fields"]["origination_fee"] == 500.0


def test_credit_offer_extracts_bt_fee_and_promo_apr():
    text = """APR: 24.99%
Promo APR: 0% for 12 months
Balance transfer fee: 3%
Annual fee: $0
Expires: 2026-12-31
"""
    parsed = parse_credit_card_offer(text)
    assert parsed["fields"]["apr"] == 24.99
    assert parsed["fields"]["promo_apr"] == 0.0
    assert parsed["fields"]["balance_transfer_fee_percent"] == 3.0
    assert parsed["fields"].get("expiration_date") == "2026-12-31"


def test_draft_extraction_does_not_affect_recommendations(session, tmp_path, monkeypatch):
    from app.db.models import CreditCard

    card = CreditCard(
        issuer="Visa", card_name="R", balance=3000, apr=22, minimum_payment=90, credit_limit=8000
    )
    session.add(card)
    session.commit()

    offer = submit_offer(
        session,
        {
            "provider": "Citi",
            "product_type": "balance_transfer",
            "apr": 22,
            "source_url": "https://citi.com",
            "linked_account_type": "credit_card",
            "linked_account_id": card.id,
        },
    )
    ev = _store(
        session,
        tmp_path,
        monkeypatch,
        "offer.txt",
        b"APR: 0%\nBalance transfer fee: 3%\n",
        "offer_terms",
        linked_entity_type="financial_offer",
        linked_entity_id=offer.id,
    )
    result = run_extraction(session, ev.id)
    assert result["status"] in ("draft", "needs_review")
    session.refresh(offer)
    assert offer.estimated_apr == 22
    assert offer.intake_status == INTAKE_PENDING

    snap = build_financial_snapshot(session)
    ctx = build_research_context_from_snapshot(snap)
    offers, _ = get_research_offers_for_recommendations(session, ctx)
    assert not any(o.get("intake_offer_id") == offer.id for o in offers)
    recs = session.exec(select(Recommendation).where(Recommendation.status == "active")).all()
    assert not any(json.loads(r.math_json or "{}").get("offer_id") == offer.id for r in recs)


def test_approved_offer_extraction_updates_pending_offer_fields(session, tmp_path, monkeypatch):
    offer = FinancialOffer(
        provider="Citi",
        product_type="balance_transfer",
        estimated_apr=22.0,
        intake_source="user_intake",
        intake_status=INTAKE_PENDING,
    )
    session.add(offer)
    session.commit()
    session.refresh(offer)

    text = b"APR: 0%\nPromo APR: 0% for 18 months\nBalance transfer fee: 3%\n"
    ev = _store(
        session,
        tmp_path,
        monkeypatch,
        "bt.txt",
        text,
        "offer_terms",
        linked_entity_type="financial_offer",
        linked_entity_id=offer.id,
    )
    payload = run_extraction(session, ev.id)
    from app.core.evidence_extraction import get_extraction

    record = get_extraction(session, payload["id"])
    approve_extraction(
        session,
        record.id,
        field_overrides={"apr": 0.0, "balance_transfer_fee_percent": 3.0},
    )
    session.refresh(offer)
    assert offer.estimated_apr == 0.0
    assert offer.balance_transfer_fee_percent == 3.0
    assert offer.intake_status == INTAKE_PENDING
