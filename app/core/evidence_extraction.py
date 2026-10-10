"""Deterministic evidence → draft structured extraction (review before apply)."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import UTC, date, datetime
from typing import Any

from sqlmodel import Session, select

from app.config import resolve_state_path, settings
from app.core.evidence import extension_of, get_evidence, read_file
from app.db.models import (
    Bill,
    EvidenceExtraction,
    EvidenceFile,
    FinancialOffer,
)
from app.events.bus import event_bus
from app.events.types import Event, EventType

EXTRACTION_DRAFT = "draft"
EXTRACTION_NEEDS_REVIEW = "needs_review"
EXTRACTION_FAILED = "failed"
EXTRACTION_APPROVED = "approved"
EXTRACTION_REJECTED = "rejected"

LOW_CONFIDENCE_THRESHOLD = 0.6
CONF_HIGH = 0.92
CONF_MED = 0.72
CONF_LOW = 0.38

EVIDENCE_TO_EXTRACTION: dict[str, str] = {
    "grocery_receipt": "grocery_receipt",
    "insurance_quote": "insurance_quote",
    "loan_quote": "loan_quote",
    "mortgage_quote": "mortgage_quote",
    "offer_terms": "credit_card_offer",
    "bill_statement": "bill_statement",
    "rate_source": "loan_quote",
}

_MONEY = re.compile(r"\$?\s*([\d,]+\.?\d*)")
_PCT = re.compile(r"([\d.]+)\s*%")
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _parse_money(text: str) -> float | None:
    m = _MONEY.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_pct(text: str) -> float | None:
    m = _PCT.search(text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _parse_date_line(text: str) -> date | None:
    m = _DATE.search(text)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            return None
    for fmt in ("%m/%d/%Y", "%m-%d-%Y"):
        dm = re.search(r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", text)
        if dm:
            try:
                return datetime.strptime(dm.group(1), fmt).date()
            except ValueError:
                continue
    return None


def ocr_image_bytes(data: bytes) -> str:
    """Optional OCR for images when EVIDENCE_OCR_ENABLED=1."""
    if not settings.evidence_ocr_enabled:
        return ""
    try:
        import pytesseract  # type: ignore[import-untyped]
        from PIL import Image  # type: ignore[import-untyped]
    except ImportError:
        return ""
    try:
        img = Image.open(io.BytesIO(data))
        return (pytesseract.image_to_string(img) or "")[:50_000]
    except Exception:
        return ""


def _evidence_text(record: EvidenceFile) -> str:
    text = (record.extracted_text or "").strip()
    if text:
        return text
    if not record.storage_path:
        return ""
    path = Path_for_storage(record.storage_path)
    if not path.exists():
        return ""
    data = read_file(path)
    ext = extension_of(record.filename)
    if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return ocr_image_bytes(data)
    return ""


def Path_for_storage(storage_path: str):
    from pathlib import Path

    p = Path(storage_path)
    if p.is_absolute():
        return p
    return resolve_state_path(storage_path)


def parse_grocery_receipt(text: str, *, filename: str = "") -> dict[str, Any]:
    fields: dict[str, Any] = {}
    confidence: dict[str, float] = {}
    line_items: list[dict[str, Any]] = []
    warnings: list[str] = []

    if filename.lower().endswith(".csv") or ("," in text and "\n" in text):
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        if rows and rows[0]:
            header = [h.strip().lower() for h in rows[0]]
            name_idx = next((i for i, h in enumerate(header) if h in ("item", "name", "product")), 0)
            price_idx = next((i for i, h in enumerate(header) if h in ("price", "unit_price", "amount")), 1)
            for row in rows[1:]:
                if len(row) <= max(name_idx, price_idx):
                    continue
                price = _parse_money(row[price_idx])
                if price is None:
                    continue
                line_items.append({"name": row[name_idx].strip(), "price": price})
                confidence[f"line_item:{len(line_items)}"] = CONF_HIGH
        if line_items:
            fields["store"] = rows[0][0] if rows and header[0] not in ("item", "name") else ""
            confidence["store"] = CONF_LOW if fields.get("store") else CONF_LOW
    else:
        for line in text.splitlines():
            raw = line.strip()
            if not raw:
                continue
            low = raw.lower()
            if low.startswith("store:"):
                fields["store"] = raw.split(":", 1)[1].strip()
                confidence["store"] = CONF_HIGH
            elif low.startswith("date:"):
                d = _parse_date_line(raw)
                if d:
                    fields["date"] = d.isoformat()
                    confidence["date"] = CONF_HIGH
            elif low.startswith("total:"):
                total = _parse_money(raw)
                if total is not None:
                    fields["total"] = total
                    confidence["total"] = CONF_HIGH
            else:
                parts = re.split(r"\s{2,}|\t|,", raw, maxsplit=1)
                if len(parts) == 2:
                    price = _parse_money(parts[1])
                    if price is not None:
                        line_items.append({"name": parts[0].strip(), "price": price})
                        confidence[f"line_item:{len(line_items)}"] = CONF_MED
                else:
                    m = re.match(r"^(.+?)\s+(\$?\d[\d,.]*)$", raw)
                    if m:
                        price = _parse_money(m.group(2))
                        if price is not None:
                            line_items.append({"name": m.group(1).strip(), "price": price})
                            confidence[f"line_item:{len(line_items)}"] = CONF_MED

    if not line_items:
        warnings.append("No line items parsed from receipt")
    return {
        "fields": fields,
        "line_items": line_items,
        "confidence": confidence,
        "warnings": warnings,
    }


def parse_bill_statement(text: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    confidence: dict[str, float] = {}
    warnings: list[str] = []
    for line in text.splitlines():
        low = line.lower()
        if "provider:" in low or low.startswith("biller:"):
            fields["provider"] = line.split(":", 1)[1].strip()
            confidence["provider"] = CONF_HIGH
        elif "due" in low and "date" in low:
            d = _parse_date_line(line)
            if d:
                fields["due_date"] = d.isoformat()
                confidence["due_date"] = CONF_HIGH
        elif "amount" in low or "balance due" in low:
            amt = _parse_money(line)
            if amt is not None:
                fields["amount"] = amt
                confidence["amount"] = CONF_HIGH
        elif "account type" in low or low.startswith("type:"):
            fields["account_type"] = line.split(":", 1)[-1].strip()
            confidence["account_type"] = CONF_MED
    if not fields:
        warnings.append("Could not parse bill fields")
    return {"fields": fields, "line_items": [], "confidence": confidence, "warnings": warnings}


def parse_loan_quote(text: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    confidence: dict[str, float] = {}
    warnings: list[str] = []
    for line in text.splitlines():
        low = line.lower()
        if "apr" in low:
            pct = _parse_pct(line)
            if pct is not None:
                fields["apr"] = pct
                confidence["apr"] = CONF_HIGH
        elif "term" in low:
            tm = re.search(r"(\d+)\s*month", low)
            if tm:
                fields["term_months"] = int(tm.group(1))
                confidence["term_months"] = CONF_HIGH
        elif "payment" in low:
            pay = _parse_money(line)
            if pay is not None:
                fields["payment"] = pay
                confidence["payment"] = CONF_HIGH
        elif "origination" in low:
            fee = _parse_money(line)
            if fee is not None:
                fields["origination_fee"] = fee
                confidence["origination_fee"] = CONF_HIGH
    if not fields:
        warnings.append("Could not parse loan quote fields")
    return {"fields": fields, "line_items": [], "confidence": confidence, "warnings": warnings}


def parse_mortgage_quote(text: str) -> dict[str, Any]:
    parsed = parse_loan_quote(text)
    fields = parsed["fields"]
    confidence = parsed["confidence"]
    warnings = list(parsed["warnings"])
    for line in text.splitlines():
        low = line.lower()
        if "rate" in low and "apr" not in low:
            pct = _parse_pct(line)
            if pct is not None:
                fields["rate"] = pct
                confidence["rate"] = CONF_MED
        elif "closing" in low:
            cost = _parse_money(line)
            if cost is not None:
                fields["closing_costs"] = cost
                confidence["closing_costs"] = CONF_HIGH
    return {"fields": fields, "line_items": [], "confidence": confidence, "warnings": warnings}


def parse_insurance_quote(text: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    confidence: dict[str, float] = {}
    warnings: list[str] = []
    for line in text.splitlines():
        low = line.lower()
        if low.startswith("provider:") or low.startswith("carrier:"):
            fields["provider"] = line.split(":", 1)[1].strip()
            confidence["provider"] = CONF_HIGH
        elif "premium" in low:
            prem = _parse_money(line)
            if prem is not None:
                fields["premium"] = prem
                confidence["premium"] = CONF_HIGH
        elif "deductible" in low:
            ded = _parse_money(line)
            if ded is not None:
                fields["deductible"] = ded
                confidence["deductible"] = CONF_HIGH
        elif "term" in low:
            fields["term"] = line.split(":", 1)[-1].strip()
            confidence["term"] = CONF_MED
        elif "coverage" in low:
            fields["coverage_notes"] = line.split(":", 1)[-1].strip()
            confidence["coverage_notes"] = CONF_MED
    if not fields:
        warnings.append("Could not parse insurance quote fields")
    return {"fields": fields, "line_items": [], "confidence": confidence, "warnings": warnings}


def parse_credit_card_offer(text: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    confidence: dict[str, float] = {}
    warnings: list[str] = []
    for line in text.splitlines():
        low = line.lower()
        if "promo" in low and "apr" in low:
            pct = _parse_pct(line)
            if pct is not None:
                fields["promo_apr"] = pct
                confidence["promo_apr"] = CONF_HIGH
            tm = re.search(r"(\d+)\s*month", low)
            if tm:
                fields["promo_months"] = int(tm.group(1))
                confidence["promo_months"] = CONF_MED
        elif "apr" in low:
            pct = _parse_pct(line)
            if pct is not None and "apr" not in fields:
                fields["apr"] = pct
                confidence["apr"] = CONF_HIGH
        elif "balance transfer" in low or "bt fee" in low:
            pct = _parse_pct(line)
            if pct is not None:
                fields["balance_transfer_fee_percent"] = pct
                confidence["balance_transfer_fee_percent"] = CONF_HIGH
            else:
                fee = _parse_money(line)
                if fee is not None:
                    fields["balance_transfer_fee_percent"] = fee
                    confidence["balance_transfer_fee_percent"] = CONF_LOW
        elif "annual fee" in low:
            fee = _parse_money(line)
            fields["annual_fee"] = fee if fee is not None else 0.0
            confidence["annual_fee"] = CONF_HIGH if fee is not None else CONF_MED
        elif "expir" in low:
            d = _parse_date_line(line)
            if d:
                fields["expiration_date"] = d.isoformat()
                confidence["expiration_date"] = CONF_HIGH
    if not fields:
        warnings.append("Could not parse credit card offer fields")
    return {"fields": fields, "line_items": [], "confidence": confidence, "warnings": warnings}


PARSERS = {
    "grocery_receipt": parse_grocery_receipt,
    "bill_statement": parse_bill_statement,
    "loan_quote": parse_loan_quote,
    "mortgage_quote": parse_mortgage_quote,
    "insurance_quote": parse_insurance_quote,
    "credit_card_offer": parse_credit_card_offer,
}


def _resolve_status(fields: dict, confidence: dict, warnings: list) -> str:
    if not fields and not warnings:
        return EXTRACTION_FAILED
    vals = [v for k, v in confidence.items() if v is not None]
    if vals and min(vals) < LOW_CONFIDENCE_THRESHOLD:
        return EXTRACTION_NEEDS_REVIEW
    if warnings and not fields:
        return EXTRACTION_FAILED
    return EXTRACTION_DRAFT


def _linked_suggestions(evidence: EvidenceFile, extraction_type: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if evidence.linked_entity_type and evidence.linked_entity_id:
        out.append(
            {
                "linked_entity_type": evidence.linked_entity_type,
                "linked_entity_id": evidence.linked_entity_id,
            }
        )
    if extraction_type in ("credit_card_offer", "loan_quote", "mortgage_quote", "insurance_quote"):
        if not out:
            out.append({"linked_entity_type": "financial_offer", "linked_entity_id": ""})
    if extraction_type == "grocery_receipt":
        if not out:
            out.append({"linked_entity_type": "grocery_price_observation", "linked_entity_id": ""})
    return out


def run_extraction(session: Session, evidence_id: str) -> dict[str, Any]:
    """Parse evidence text into a draft extraction record."""
    evidence = get_evidence(session, evidence_id)
    if not evidence:
        raise ValueError("evidence not found")

    event_bus.emit(
        Event(
            event_type=EventType.EVIDENCE_EXTRACTION_STARTED.value,
            payload={"evidence_id": evidence_id},
        )
    )

    extraction_type = EVIDENCE_TO_EXTRACTION.get(evidence.evidence_type)
    if not extraction_type:
        result = _fail_payload(evidence_id, "", ["Unsupported evidence type for extraction"])
        _persist_extraction(session, evidence_id, extraction_type or "unknown", result)
        event_bus.emit(
            Event(
                event_type=EventType.EVIDENCE_EXTRACTION_FAILED.value,
                payload={"evidence_id": evidence_id},
            )
        )
        return result

    text = _evidence_text(evidence)
    if not text.strip():
        warnings = ["No text available — enable OCR or upload TXT/CSV/PDF"]
        if evidence.filename and extension_of(evidence.filename) in {".png", ".jpg", ".jpeg"}:
            if not settings.evidence_ocr_enabled:
                warnings.append("OCR disabled (set EVIDENCE_OCR_ENABLED=1 to enable)")
        result = _fail_payload(evidence_id, extraction_type, warnings)
        _persist_extraction(session, evidence_id, extraction_type, result)
        event_bus.emit(
            Event(
                event_type=EventType.EVIDENCE_EXTRACTION_FAILED.value,
                payload={"evidence_id": evidence_id},
            )
        )
        return result

    parser = PARSERS[extraction_type]
    if extraction_type == "grocery_receipt":
        parsed = parser(text, filename=evidence.filename)
    else:
        parsed = parser(text)

    status = _resolve_status(parsed["fields"], parsed["confidence"], parsed["warnings"])
    payload = {
        "evidence_id": evidence_id,
        "extraction_type": extraction_type,
        "status": status,
        "fields": parsed["fields"],
        "line_items": parsed["line_items"],
        "confidence": parsed["confidence"],
        "warnings": parsed["warnings"],
        "linked_entity_suggestions": _linked_suggestions(evidence, extraction_type),
    }

    record = _persist_extraction(session, evidence_id, extraction_type, payload)
    out = extraction_to_dict(record)

    event_bus.emit(
        Event(
            event_type=EventType.EVIDENCE_EXTRACTION_COMPLETED.value,
            payload={"evidence_id": evidence_id, "extraction_id": record.id, "status": status},
        )
    )
    if status == EXTRACTION_FAILED:
        event_bus.emit(
            Event(
                event_type=EventType.EVIDENCE_EXTRACTION_FAILED.value,
                payload={"evidence_id": evidence_id},
            )
        )
    return out


def _fail_payload(evidence_id: str, extraction_type: str, warnings: list[str]) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "extraction_type": extraction_type,
        "status": EXTRACTION_FAILED,
        "fields": {},
        "line_items": [],
        "confidence": {},
        "warnings": warnings,
        "linked_entity_suggestions": [],
    }


def _persist_extraction(
    session: Session,
    evidence_id: str,
    extraction_type: str,
    payload: dict[str, Any],
) -> EvidenceExtraction:
    pending = [
        e
        for e in session.exec(
            select(EvidenceExtraction).where(EvidenceExtraction.evidence_id == evidence_id)
        ).all()
        if e.status in (EXTRACTION_DRAFT, EXTRACTION_NEEDS_REVIEW, EXTRACTION_FAILED)
    ]
    for existing in pending:
        session.delete(existing)
    if pending:
        session.commit()

    record = EvidenceExtraction(
        evidence_id=evidence_id,
        extraction_type=extraction_type,
        status=payload["status"],
        fields_json=json.dumps(payload.get("fields") or {}),
        line_items_json=json.dumps(payload.get("line_items") or []),
        confidence_json=json.dumps(payload.get("confidence") or {}),
        warnings_json=json.dumps(payload.get("warnings") or []),
        suggestions_json=json.dumps(payload.get("linked_entity_suggestions") or []),
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def extraction_to_dict(record: EvidenceExtraction) -> dict[str, Any]:
    return {
        "id": record.id,
        "evidence_id": record.evidence_id,
        "extraction_type": record.extraction_type,
        "status": record.status,
        "fields": json.loads(record.fields_json or "{}"),
        "line_items": json.loads(record.line_items_json or "[]"),
        "confidence": json.loads(record.confidence_json or "{}"),
        "warnings": json.loads(record.warnings_json or "[]"),
        "linked_entity_suggestions": json.loads(record.suggestions_json or "[]"),
        "reviewed_at": record.reviewed_at.isoformat() + "Z" if record.reviewed_at else None,
        "reviewer_notes": record.reviewer_notes,
        "created_at": record.created_at.isoformat() + "Z",
    }


def list_extractions(
    session: Session,
    *,
    status: str | None = None,
    evidence_id: str | None = None,
) -> list[EvidenceExtraction]:
    rows = list(session.exec(select(EvidenceExtraction)).all())
    if status:
        rows = [r for r in rows if r.status == status]
    if evidence_id:
        rows = [r for r in rows if r.evidence_id == evidence_id]
    return sorted(rows, key=lambda r: r.created_at, reverse=True)


def get_extraction(session: Session, extraction_id: str) -> EvidenceExtraction | None:
    return session.get(EvidenceExtraction, extraction_id)


def update_extraction_fields(
    session: Session,
    extraction_id: str,
    *,
    fields: dict[str, Any] | None = None,
    line_items: list[dict[str, Any]] | None = None,
    confidence: dict[str, float] | None = None,
) -> EvidenceExtraction:
    record = session.get(EvidenceExtraction, extraction_id)
    if not record:
        raise ValueError("extraction not found")
    if record.status not in (EXTRACTION_DRAFT, EXTRACTION_NEEDS_REVIEW):
        raise ValueError("can only edit draft or needs_review extractions")

    if fields is not None:
        record.fields_json = json.dumps(fields)
    if line_items is not None:
        record.line_items_json = json.dumps(line_items)
    if confidence is not None:
        record.confidence_json = json.dumps(confidence)
    else:
        conf = json.loads(record.confidence_json or "{}")
        if fields:
            for k in fields:
                conf[k] = max(conf.get(k, 0), CONF_HIGH)
        record.confidence_json = json.dumps(conf)

    record.status = EXTRACTION_DRAFT
    record.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def approve_extraction(
    session: Session,
    extraction_id: str,
    *,
    reviewer_notes: str = "",
    field_overrides: dict[str, Any] | None = None,
) -> EvidenceExtraction:
    """Apply extraction to household data after human approval."""
    record = session.get(EvidenceExtraction, extraction_id)
    if not record:
        raise ValueError("extraction not found")
    if record.status not in (EXTRACTION_DRAFT, EXTRACTION_NEEDS_REVIEW):
        raise ValueError(f"cannot approve extraction in status {record.status}")

    fields = json.loads(record.fields_json or "{}")
    if field_overrides:
        fields.update(field_overrides)
        record.fields_json = json.dumps(fields)

    conf = json.loads(record.confidence_json or "{}")
    low = [k for k, v in conf.items() if float(v) < LOW_CONFIDENCE_THRESHOLD]
    if low and not field_overrides:
        raise ValueError(f"low-confidence fields require manual correction: {', '.join(low)}")

    evidence = get_evidence(session, record.evidence_id)
    if not evidence:
        raise ValueError("linked evidence not found")

    line_items = json.loads(record.line_items_json or "[]")
    _apply_extraction(session, record, evidence, fields, line_items)

    record.status = EXTRACTION_APPROVED
    record.reviewed_at = datetime.now(UTC).replace(tzinfo=None)
    if reviewer_notes:
        record.reviewer_notes = reviewer_notes.strip()
    record.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(record)
    session.commit()
    session.refresh(record)

    event_bus.emit(
        Event(
            event_type=EventType.EVIDENCE_EXTRACTION_APPROVED.value,
            payload={"extraction_id": record.id, "evidence_id": record.evidence_id},
        )
    )
    return record


def reject_extraction(
    session: Session,
    extraction_id: str,
    *,
    reviewer_notes: str = "",
) -> EvidenceExtraction:
    record = session.get(EvidenceExtraction, extraction_id)
    if not record:
        raise ValueError("extraction not found")
    if record.status not in (EXTRACTION_DRAFT, EXTRACTION_NEEDS_REVIEW, EXTRACTION_FAILED):
        raise ValueError(f"cannot reject extraction in status {record.status}")

    record.status = EXTRACTION_REJECTED
    record.reviewed_at = datetime.now(UTC).replace(tzinfo=None)
    if reviewer_notes:
        record.reviewer_notes = reviewer_notes.strip()
    session.add(record)
    session.commit()
    session.refresh(record)

    event_bus.emit(
        Event(
            event_type=EventType.EVIDENCE_EXTRACTION_REJECTED.value,
            payload={"extraction_id": record.id},
        )
    )
    return record


def _apply_extraction(
    session: Session,
    record: EvidenceExtraction,
    evidence: EvidenceFile,
    fields: dict[str, Any],
    line_items: list[dict[str, Any]],
) -> None:
    if record.extraction_type == "grocery_receipt":
        # Grocery product writes were retired in D015.  Preserve receipt
        # parsing for historical evidence compatibility, but never create or
        # mutate Grocery* rows from an approved extraction.
        return
    elif record.extraction_type in (
        "credit_card_offer",
        "loan_quote",
        "mortgage_quote",
        "insurance_quote",
    ):
        _apply_offer(session, evidence, record.extraction_type, fields)
    elif record.extraction_type == "bill_statement":
        _apply_bill(session, evidence, fields)

def _apply_offer(
    session: Session,
    evidence: EvidenceFile,
    extraction_type: str,
    fields: dict[str, Any],
) -> None:
    offer_id = evidence.linked_entity_id
    if evidence.linked_entity_type != "financial_offer" or not offer_id:
        return
    offer = session.get(FinancialOffer, offer_id)
    if not offer or offer.intake_status != "pending_review":
        return

    if extraction_type == "credit_card_offer":
        if fields.get("apr") is not None:
            offer.estimated_apr = float(fields["apr"])
        if fields.get("promo_apr") is not None:
            offer.estimated_apr = float(fields["promo_apr"])
        if fields.get("balance_transfer_fee_percent") is not None:
            offer.balance_transfer_fee_percent = float(fields["balance_transfer_fee_percent"])
        if fields.get("annual_fee") is not None:
            offer.annual_fee = float(fields["annual_fee"])
        if fields.get("expiration_date"):
            try:
                offer.expiration_date = date.fromisoformat(str(fields["expiration_date"])[:10])
            except ValueError:
                pass
    elif extraction_type == "loan_quote":
        if fields.get("apr") is not None:
            offer.estimated_apr = float(fields["apr"])
        if fields.get("term_months") is not None:
            offer.term_months = int(fields["term_months"])
        if fields.get("payment") is not None:
            offer.monthly_payment_estimate = float(fields["payment"])
        if fields.get("origination_fee") is not None:
            offer.origination_fee = float(fields["origination_fee"])
    elif extraction_type == "mortgage_quote":
        if fields.get("apr") is not None:
            offer.estimated_apr = float(fields["apr"])
        elif fields.get("rate") is not None:
            offer.estimated_apr = float(fields["rate"])
        if fields.get("term_months") is not None:
            offer.term_months = int(fields["term_months"])
        if fields.get("payment") is not None:
            offer.monthly_payment_estimate = float(fields["payment"])
        if fields.get("closing_costs") is not None:
            offer.closing_costs = float(fields["closing_costs"])
    elif extraction_type == "insurance_quote":
        if fields.get("provider"):
            offer.provider = str(fields["provider"])
        if fields.get("premium") is not None:
            offer.monthly_payment_estimate = float(fields["premium"])
        if fields.get("term"):
            offer.term = str(fields["term"])
        notes = fields.get("coverage_notes") or ""
        if fields.get("deductible") is not None:
            notes = f"{notes} deductible=${fields['deductible']}".strip()
        if notes:
            offer.notes = f"{offer.notes}\n{notes}".strip()

    offer.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(offer)
    session.commit()


def _apply_bill(session: Session, evidence: EvidenceFile, fields: dict[str, Any]) -> None:
    if evidence.linked_entity_type == "bill" and evidence.linked_entity_id:
        bill = session.get(Bill, evidence.linked_entity_id)
        if bill:
            if fields.get("amount") is not None:
                bill.amount = float(fields["amount"])
            if fields.get("due_date"):
                try:
                    bill.due_date = date.fromisoformat(str(fields["due_date"])[:10])
                except ValueError:
                    pass
            session.add(bill)
            session.commit()
        return
    name = (fields.get("provider") or "Imported bill").strip()
    amount = float(fields.get("amount") or 0)
    due = None
    if fields.get("due_date"):
        try:
            due = date.fromisoformat(str(fields["due_date"])[:10])
        except ValueError:
            due = None
    session.add(Bill(name=name, amount=amount, due_date=due, required=True))
    session.commit()


def draft_extractions_affect_cfo() -> bool:
    """Draft extractions never affect CFO engines until approved."""
    return False
