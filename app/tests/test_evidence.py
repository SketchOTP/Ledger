"""Evidence intake tests."""


import pytest

from app.core.evidence import (
    MAX_FILE_BYTES,
    approve_evidence,
    offer_has_approved_evidence,
    offer_source_proof_satisfied,
    sha256_hex,
    store_evidence,
    validate_upload,
)
from app.core.offer_intake import INTAKE_PENDING, approve_offer
from app.db.models import FinancialOffer


def test_upload_stores_hash(session, tmp_path, monkeypatch):
    from app.core import evidence as ev_mod

    monkeypatch.setattr(ev_mod, "EVIDENCE_DIR", tmp_path / "evidence")
    data = b"price,milk,3.49\n"
    record = store_evidence(
        session,
        filename="prices.csv",
        content_type="text/csv",
        data=data,
        evidence_type="grocery_receipt",
    )
    assert record.sha256 == sha256_hex(data)
    assert record.status == "pending_review"
    path = tmp_path / "evidence" / f"{record.id}_prices.csv"
    assert path.exists()
    assert path.read_bytes() == data


def test_unsafe_extension_rejected():
    with pytest.raises(ValueError, match="unsafe"):
        validate_upload("malware.exe", 10, b"x")


def test_oversized_file_rejected():
    with pytest.raises(ValueError, match="maximum size"):
        validate_upload("big.txt", MAX_FILE_BYTES + 1, b"x" * (MAX_FILE_BYTES + 1))


def test_pending_evidence_does_not_approve_offer(session, tmp_path, monkeypatch):
    from app.core import evidence as ev_mod

    monkeypatch.setattr(ev_mod, "EVIDENCE_DIR", tmp_path / "evidence")

    offer = FinancialOffer(
        provider="Citi",
        product_type="balance_transfer",
        estimated_apr=0,
        estimated_savings=100,
        intake_source="user_intake",
        intake_status=INTAKE_PENDING,
    )
    session.add(offer)
    session.commit()
    session.refresh(offer)

    store_evidence(
        session,
        filename="terms.txt",
        content_type="text/plain",
        data=b"0% APR 12 months",
        evidence_type="offer_terms",
        linked_entity_type="financial_offer",
        linked_entity_id=offer.id,
        source_url="https://citi.com/offer",
    )
    assert offer.intake_status == INTAKE_PENDING
    with pytest.raises(ValueError, match="approved linked evidence"):
        approve_offer(session, offer.id)


def test_approved_evidence_can_satisfy_offer_source_requirement(session, tmp_path, monkeypatch):
    from app.core import evidence as ev_mod

    monkeypatch.setattr(ev_mod, "EVIDENCE_DIR", tmp_path / "evidence")

    offer = FinancialOffer(
        provider="Ally",
        product_type="hysa",
        apy=4.5,
        estimated_savings=50,
        intake_source="user_intake",
        intake_status=INTAKE_PENDING,
    )
    session.add(offer)
    session.commit()
    session.refresh(offer)

    record = store_evidence(
        session,
        filename="rate.txt",
        content_type="text/plain",
        data=b"APY 4.50% as of May 2026",
        evidence_type="rate_source",
        linked_entity_type="financial_offer",
        linked_entity_id=offer.id,
        source_url="https://ally.com/rates",
    )
    assert not offer_source_proof_satisfied(session, offer)

    approve_evidence(session, record.id)
    session.refresh(offer)
    assert offer_has_approved_evidence(session, offer.id)
    assert offer_source_proof_satisfied(session, offer)

    approved = approve_offer(session, offer.id)
    assert approved.intake_status == "approved"


def test_text_extraction_works_for_txt_csv(session, tmp_path, monkeypatch):
    from app.core import evidence as ev_mod

    monkeypatch.setattr(ev_mod, "EVIDENCE_DIR", tmp_path / "evidence")

    txt = store_evidence(
        session,
        filename="note.txt",
        content_type="text/plain",
        data=b"Hello proof",
        evidence_type="bill_statement",
    )
    assert "Hello proof" in txt.extracted_text

    csv_row = store_evidence(
        session,
        filename="bill.csv",
        content_type="text/csv",
        data=b"a,b\n1,2\n",
        evidence_type="bill_statement",
    )
    assert "a, b" in csv_row.extracted_text or "a,b" in csv_row.extracted_text
