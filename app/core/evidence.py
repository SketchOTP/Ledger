"""File evidence intake — storage, validation, text extraction, review gate."""

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from app.config import settings
from app.db.models import EvidenceFile, FinancialOffer
from app.events.bus import event_bus
from app.events.types import Event, EventType

EVIDENCE_DIR = settings.evidence_dir
MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB

EVIDENCE_TYPES: tuple[str, ...] = (
    "offer_terms",
    "bill_statement",
    "grocery_receipt",
    "insurance_quote",
    "loan_quote",
    "mortgage_quote",
    "rate_source",
)

LINKED_ENTITY_TYPES: tuple[str, ...] = (
    "financial_offer",
    "bill",
    "grocery_price_observation",
    "grocery_item",
    "debt",
    "mortgage",
    "credit_card",
    "research_ingestion_finding",
)

STATUS_PENDING = "pending_review"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {".txt", ".csv", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif"}
)
BLOCKED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".exe",
        ".bat",
        ".cmd",
        ".com",
        ".sh",
        ".bash",
        ".ps1",
        ".php",
        ".js",
        ".html",
        ".htm",
        ".svg",
        ".zip",
        ".rar",
        ".7z",
        ".dll",
        ".msi",
        ".jar",
        ".vbs",
        ".scr",
    }
)

OFFER_EVIDENCE_TYPES: frozenset[str] = frozenset(
    {"offer_terms", "insurance_quote", "loan_quote", "mortgage_quote", "rate_source"}
)


def ensure_evidence_dir() -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    return EVIDENCE_DIR


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_filename(name: str) -> str:
    base = Path(name).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return cleaned[:180] or "upload.bin"


def extension_of(filename: str) -> str:
    return Path(filename).suffix.lower()


def validate_upload(filename: str, size_bytes: int, data: bytes) -> None:
    ext = extension_of(filename)
    if ext in BLOCKED_EXTENSIONS:
        raise ValueError(f"unsafe file extension: {ext or '(none)'}")
    if ext and ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"extension not allowed: {ext}")
    if not ext:
        raise ValueError("file must have a recognized extension")
    if size_bytes <= 0:
        raise ValueError("empty file")
    if size_bytes > MAX_FILE_BYTES:
        raise ValueError(f"file exceeds maximum size of {MAX_FILE_BYTES} bytes")


def validate_evidence_type(evidence_type: str) -> None:
    if evidence_type not in EVIDENCE_TYPES:
        raise ValueError(f"evidence_type must be one of: {', '.join(EVIDENCE_TYPES)}")


def validate_linked_entity(linked_entity_type: str, linked_entity_id: str) -> None:
    if linked_entity_type and linked_entity_type not in LINKED_ENTITY_TYPES:
        raise ValueError(f"linked_entity_type must be one of: {', '.join(LINKED_ENTITY_TYPES)}")
    if linked_entity_type and not (linked_entity_id or "").strip():
        raise ValueError("linked_entity_id required when linked_entity_type is set")


def extract_text(filename: str, data: bytes, content_type: str = "") -> str:
    """Extract text from TXT/CSV/PDF where practical."""
    ext = extension_of(filename)
    if ext == ".txt":
        return data.decode("utf-8", errors="replace")[:50_000]
    if ext == ".csv":
        return _extract_csv_text(data)
    if ext == ".pdf":
        return _extract_pdf_text(data)
    return ""


def _extract_csv_text(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = []
    for i, row in enumerate(reader):
        if i >= 200:
            rows.append("…")
            break
        rows.append(", ".join(row))
    return "\n".join(rows)[:50_000]


def _extract_pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore[import-untyped]
    except ImportError:
        return ""
    try:
        reader = PdfReader(io.BytesIO(data))
        parts: list[str] = []
        for page in reader.pages[:25]:
            parts.append(page.extract_text() or "")
        return "\n".join(parts)[:50_000]
    except Exception:
        return ""


def storage_path(evidence_id: str, filename: str) -> Path:
    safe = _safe_filename(filename)
    return ensure_evidence_dir() / f"{evidence_id}_{safe}"


def write_file(path: Path, data: bytes) -> None:
    path.write_bytes(data)


def read_file(path: Path) -> bytes:
    return path.read_bytes()


def evidence_to_dict(record: EvidenceFile) -> dict[str, Any]:
    return {
        "id": record.id,
        "filename": record.filename,
        "content_type": record.content_type,
        "sha256": record.sha256,
        "size_bytes": record.size_bytes,
        "evidence_type": record.evidence_type,
        "linked_entity_type": record.linked_entity_type,
        "linked_entity_id": record.linked_entity_id,
        "source_url": record.source_url,
        "uploaded_at": record.uploaded_at.isoformat() + "Z",
        "extracted_text": record.extracted_text,
        "extracted_text_preview": (record.extracted_text or "")[:2000],
        "status": record.status,
        "reviewed_at": record.reviewed_at.isoformat() + "Z" if record.reviewed_at else None,
        "reviewer_notes": record.reviewer_notes,
    }


def store_evidence(
    session: Session,
    *,
    filename: str,
    content_type: str,
    data: bytes,
    evidence_type: str,
    linked_entity_type: str = "",
    linked_entity_id: str = "",
    source_url: str = "",
) -> EvidenceFile:
    validate_evidence_type(evidence_type)
    validate_linked_entity(linked_entity_type, linked_entity_id)
    size = len(data)
    validate_upload(filename, size, data)

    digest = sha256_hex(data)
    extracted = extract_text(filename, data, content_type)

    record = EvidenceFile(
        filename=_safe_filename(filename),
        content_type=content_type or "application/octet-stream",
        sha256=digest,
        size_bytes=size,
        evidence_type=evidence_type,
        linked_entity_type=(linked_entity_type or "").strip(),
        linked_entity_id=(linked_entity_id or "").strip(),
        source_url=(source_url or "").strip(),
        extracted_text=extracted,
        status=STATUS_PENDING,
    )
    session.add(record)
    session.commit()
    session.refresh(record)

    path = storage_path(record.id, record.filename)
    write_file(path, data)
    try:
        record.storage_path = str(path.resolve().relative_to(settings.data_dir.resolve()))
    except ValueError:
        # Tests or explicitly configured evidence directories may be outside
        # the state root; preserve the absolute path safely.
        record.storage_path = str(path.resolve())
    session.add(record)
    session.commit()
    session.refresh(record)

    event_bus.emit(
        Event(
            event_type=EventType.EVIDENCE_UPLOADED.value,
            payload={"evidence_id": record.id, "evidence_type": record.evidence_type},
        )
    )
    if record.linked_entity_type and record.linked_entity_id:
        event_bus.emit(
            Event(
                event_type=EventType.EVIDENCE_LINKED.value,
                payload={
                    "evidence_id": record.id,
                    "linked_entity_type": record.linked_entity_type,
                    "linked_entity_id": record.linked_entity_id,
                },
            )
        )
    return record


def list_evidence(
    session: Session,
    *,
    status: str | None = None,
    linked_entity_type: str | None = None,
    linked_entity_id: str | None = None,
) -> list[EvidenceFile]:
    rows = list(session.exec(select(EvidenceFile)).all())
    if status:
        rows = [r for r in rows if r.status == status]
    if linked_entity_type:
        rows = [r for r in rows if r.linked_entity_type == linked_entity_type]
    if linked_entity_id:
        rows = [r for r in rows if r.linked_entity_id == linked_entity_id]
    return sorted(rows, key=lambda r: r.uploaded_at, reverse=True)


def get_evidence(session: Session, evidence_id: str) -> EvidenceFile | None:
    return session.get(EvidenceFile, evidence_id)


def approve_evidence(
    session: Session,
    evidence_id: str,
    *,
    reviewer_notes: str = "",
) -> EvidenceFile:
    """Approve evidence file only — never auto-approves a linked offer."""
    record = session.get(EvidenceFile, evidence_id)
    if not record:
        raise ValueError("evidence not found")
    if record.status != STATUS_PENDING:
        raise ValueError(f"cannot approve evidence in status {record.status}")

    record.status = STATUS_APPROVED
    record.reviewed_at = datetime.now(UTC).replace(tzinfo=None)
    if reviewer_notes:
        record.reviewer_notes = reviewer_notes.strip()
    session.add(record)
    session.commit()
    session.refresh(record)

    event_bus.emit(
        Event(
            event_type=EventType.EVIDENCE_APPROVED.value,
            payload={"evidence_id": record.id, "linked_entity_id": record.linked_entity_id},
        )
    )
    _sync_offer_evidence_notes(session, record)
    return record


def reject_evidence(
    session: Session,
    evidence_id: str,
    *,
    reviewer_notes: str = "",
) -> EvidenceFile:
    record = session.get(EvidenceFile, evidence_id)
    if not record:
        raise ValueError("evidence not found")
    if record.status != STATUS_PENDING:
        raise ValueError(f"cannot reject evidence in status {record.status}")

    record.status = STATUS_REJECTED
    record.reviewed_at = datetime.now(UTC).replace(tzinfo=None)
    if reviewer_notes:
        record.reviewer_notes = reviewer_notes.strip()
    session.add(record)
    session.commit()
    session.refresh(record)

    event_bus.emit(
        Event(
            event_type=EventType.EVIDENCE_REJECTED.value,
            payload={"evidence_id": record.id},
        )
    )
    return record


def _sync_offer_evidence_notes(session: Session, record: EvidenceFile) -> None:
    """Append a short proof reference on the offer — does not change intake_status."""
    if record.linked_entity_type != "financial_offer" or not record.linked_entity_id:
        return
    offer = session.get(FinancialOffer, record.linked_entity_id)
    if not offer:
        return
    ref = f"[evidence:{record.id} approved {record.filename}]"
    if ref not in (offer.evidence_notes or ""):
        offer.evidence_notes = f"{(offer.evidence_notes or '').strip()}\n{ref}".strip()
        offer.updated_at = datetime.now(UTC).replace(tzinfo=None)
        session.add(offer)
        session.commit()


def approved_evidence_for_entity(
    session: Session,
    linked_entity_type: str,
    linked_entity_id: str,
    *,
    evidence_types: frozenset[str] | None = None,
) -> list[EvidenceFile]:
    rows = list_evidence(
        session,
        status=STATUS_APPROVED,
        linked_entity_type=linked_entity_type,
        linked_entity_id=linked_entity_id,
    )
    if evidence_types:
        rows = [r for r in rows if r.evidence_type in evidence_types]
    return rows


def offer_has_approved_evidence(session: Session, offer_id: str) -> bool:
    rows = approved_evidence_for_entity(
        session,
        "financial_offer",
        offer_id,
        evidence_types=OFFER_EVIDENCE_TYPES,
    )
    for row in rows:
        if (row.source_url or "").strip() or (row.extracted_text or "").strip():
            return True
    return False


def offer_source_proof_satisfied(session: Session, offer: FinancialOffer) -> bool:
    """True when URL, notes, or approved linked evidence provides proof."""
    if (offer.source_url or "").strip() or (offer.evidence_notes or "").strip():
        return True
    return offer_has_approved_evidence(session, offer.id)
