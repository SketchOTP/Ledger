"""User-submitted financial offers — validation, review gate, recommendation eligibility."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from sqlmodel import Session, select

from app.core.offer_intake_validation import is_offer_expired, validate_offer_link
from app.db.models import FinancialOffer
from app.events.bus import event_bus
from app.events.types import Event, EventType

INTAKE_PENDING = "pending_review"
INTAKE_APPROVED = "approved"
INTAKE_REJECTED = "rejected"

SOURCE_USER = "user_intake"
SOURCE_RESEARCH = "research"

OFFER_PRODUCT_TYPES: tuple[str, ...] = (
    "credit_card",
    "balance_transfer",
    "personal_loan",
    "auto_refinance",
    "mortgage_refinance",
    "insurance",
    "hysa",
    "cd",
    "utility",
)

REQUIRED_EVIDENCE_MSG = "source_url or evidence_notes required"


def validate_offer_payload(data: dict[str, Any]) -> list[str]:
    """Return validation errors; empty list means valid."""
    errors: list[str] = []
    provider = (data.get("provider") or "").strip()
    product_type = (data.get("product_type") or "").strip()
    if not provider:
        errors.append("provider is required")
    if not product_type:
        errors.append("product_type is required")
    elif product_type not in OFFER_PRODUCT_TYPES:
        errors.append(f"product_type must be one of: {', '.join(OFFER_PRODUCT_TYPES)}")

    source_url = (data.get("source_url") or "").strip()
    evidence = (data.get("evidence_notes") or "").strip()
    if not source_url and not evidence:
        errors.append(REQUIRED_EVIDENCE_MSG)

    has_rate = any(
        data.get(k) is not None
        for k in ("apr", "estimated_apr", "apy", "monthly_payment", "monthly_payment_estimate")
    )
    if not has_rate and data.get("estimated_savings") is None:
        errors.append("apr, apy, monthly_payment, or estimated_savings required")

    exp = data.get("expiration_date")
    if exp:
        try:
            if isinstance(exp, str):
                date.fromisoformat(exp[:10])
        except ValueError:
            errors.append("expiration_date must be YYYY-MM-DD")

    return errors


def validate_offer_payload_with_session(session: Session, data: dict[str, Any]) -> list[str]:
    errors = validate_offer_payload(data)
    errors.extend(
        validate_offer_link(
            session,
            str(data.get("product_type", "")),
            data.get("linked_account_type"),
            data.get("linked_account_id"),
        )
    )
    return errors


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _offer_from_payload(data: dict[str, Any]) -> FinancialOffer:
    apr = data.get("apr")
    if apr is None:
        apr = data.get("estimated_apr")
    monthly = data.get("monthly_payment")
    if monthly is None:
        monthly = data.get("monthly_payment_estimate")

    return FinancialOffer(
        provider=str(data.get("provider", "")).strip(),
        product_type=str(data.get("product_type", "")).strip(),
        offer_name=str(data.get("offer_name") or "").strip(),
        estimated_apr=float(apr) if apr is not None else None,
        apy=float(data["apy"]) if data.get("apy") is not None else None,
        monthly_payment_estimate=float(monthly) if monthly is not None else None,
        term_months=int(data["term_months"]) if data.get("term_months") is not None else None,
        balance_transfer_fee_percent=(
            float(data["balance_transfer_fee_percent"])
            if data.get("balance_transfer_fee_percent") is not None
            else None
        ),
        annual_fee=float(data.get("annual_fee") or 0),
        origination_fee=(
            float(data["origination_fee"]) if data.get("origination_fee") is not None else None
        ),
        closing_costs=(
            float(data["closing_costs"]) if data.get("closing_costs") is not None else None
        ),
        estimated_savings=float(data.get("estimated_savings") or 0),
        source_url=str(data.get("source_url") or "").strip(),
        evidence_notes=str(data.get("evidence_notes") or "").strip(),
        expiration_date=_parse_date(data.get("expiration_date")),
        requires_credit_check=bool(data.get("requires_credit_check", True)),
        notes=str(data.get("notes") or "").strip(),
        linked_account_id=(str(data["linked_account_id"]).strip() if data.get("linked_account_id") else None),
        linked_account_type=(
            str(data["linked_account_type"]).strip() if data.get("linked_account_type") else None
        ),
        intake_source=SOURCE_USER,
        intake_status=INTAKE_PENDING,
        recommendation_status="pending",
        confidence=0.5,
        eligibility_notes=str(data.get("notes") or "")[:500],
    )


def submit_offer(session: Session, data: dict[str, Any]) -> FinancialOffer:
    errors = validate_offer_payload_with_session(session, data)
    if errors:
        raise ValueError("; ".join(errors))
    if data.get("expiration_date"):
        exp = _parse_date(data.get("expiration_date"))
        if exp and exp < date.today():
            raise ValueError("expiration_date is in the past")

    offer = _offer_from_payload(data)
    offer.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(offer)
    session.commit()
    session.refresh(offer)

    event_bus.emit(
        Event(
            event_type=EventType.OFFER_SUBMITTED.value,
            source="offer.intake",
            payload={"offer_id": offer.id, "product_type": offer.product_type},
        )
    )
    return offer


def approve_offer(
    session: Session,
    offer_id: str,
    *,
    reviewer_notes: str = "",
) -> FinancialOffer:
    offer = session.get(FinancialOffer, offer_id)
    if not offer:
        raise ValueError("offer not found")
    if offer.intake_source != SOURCE_USER:
        raise ValueError("only user_intake offers require approval")
    if offer.intake_status != INTAKE_PENDING:
        raise ValueError(f"cannot approve offer in status {offer.intake_status}")
    if is_offer_expired(offer):
        raise ValueError("cannot approve expired offer")

    from app.core.evidence import offer_source_proof_satisfied

    if not offer_source_proof_satisfied(session, offer):
        raise ValueError(
            "source_url, evidence_notes, or approved linked evidence file required before approval"
        )

    offer.intake_status = INTAKE_APPROVED
    offer.recommendation_status = "eligible"
    offer.updated_at = datetime.now(UTC).replace(tzinfo=None)
    if reviewer_notes:
        offer.notes = f"{offer.notes}\n[review] {reviewer_notes}".strip()
    session.add(offer)
    session.commit()
    session.refresh(offer)

    event_bus.emit(
        Event(
            event_type=EventType.OFFER_APPROVED.value,
            source="offer.intake",
            payload={"offer_id": offer.id},
        )
    )
    event_bus.emit(
        Event(
            event_type=EventType.OFFER_REVIEWED.value,
            source="offer.intake",
            payload={"offer_id": offer.id, "action": "approved"},
        )
    )

    # Approval is now a historical/compatibility state transition only.  V2
    # candidates are created through the canonical opportunity boundary; an
    # approved legacy offer must not generate a V1 recommendation.
    return offer


def reject_offer(
    session: Session,
    offer_id: str,
    *,
    reviewer_notes: str = "",
) -> FinancialOffer:
    offer = session.get(FinancialOffer, offer_id)
    if not offer:
        raise ValueError("offer not found")
    if offer.intake_status != INTAKE_PENDING:
        raise ValueError(f"cannot reject offer in status {offer.intake_status}")

    offer.intake_status = INTAKE_REJECTED
    offer.recommendation_status = "rejected"
    offer.updated_at = datetime.now(UTC).replace(tzinfo=None)
    if reviewer_notes:
        offer.notes = f"{offer.notes}\n[review] {reviewer_notes}".strip()
    session.add(offer)
    session.commit()
    session.refresh(offer)

    event_bus.emit(
        Event(
            event_type=EventType.OFFER_REJECTED.value,
            source="offer.intake",
            payload={"offer_id": offer.id},
        )
    )
    event_bus.emit(
        Event(
            event_type=EventType.OFFER_REVIEWED.value,
            source="offer.intake",
            payload={"offer_id": offer.id, "action": "rejected"},
        )
    )
    return offer


def list_intake_offers(
    session: Session,
    *,
    intake_status: str | None = None,
) -> list[FinancialOffer]:
    offers = [
        o
        for o in session.exec(select(FinancialOffer)).all()
        if o.intake_source == SOURCE_USER
    ]
    if intake_status:
        offers = [o for o in offers if o.intake_status == intake_status]
    return sorted(offers, key=lambda o: o.created_at, reverse=True)


def get_approved_user_offers(session: Session) -> list[FinancialOffer]:
    return [
        o
        for o in session.exec(select(FinancialOffer)).all()
        if o.intake_source == SOURCE_USER and o.intake_status == INTAKE_APPROVED
    ]


def approved_user_offers_as_research_dicts(session: Session) -> list[dict[str, Any]]:
    """Convert approved user offers to research-offer shape for refresh cycle."""
    out: list[dict[str, Any]] = []
    for o in get_approved_user_offers(session):
        out.append(offer_to_research_dict(o))
    return out


def offer_to_dict(offer: FinancialOffer, session: Session | None = None) -> dict[str, Any]:
    comparison = {}
    if offer.comparison_json:
        try:
            comparison = json.loads(offer.comparison_json)
        except json.JSONDecodeError:
            comparison = {}
    return {
        "id": offer.id,
        "provider": offer.provider,
        "product_type": offer.product_type,
        "offer_name": offer.offer_name,
        "apr": offer.estimated_apr,
        "apy": offer.apy,
        "monthly_payment": offer.monthly_payment_estimate,
        "term_months": offer.term_months,
        "balance_transfer_fee_percent": offer.balance_transfer_fee_percent,
        "annual_fee": offer.annual_fee,
        "origination_fee": offer.origination_fee,
        "closing_costs": offer.closing_costs,
        "estimated_savings": offer.estimated_savings,
        "source_url": offer.source_url,
        "evidence_notes": offer.evidence_notes,
        "expiration_date": offer.expiration_date.isoformat() if offer.expiration_date else None,
        "requires_credit_check": offer.requires_credit_check,
        "notes": offer.notes,
        "linked_account_id": offer.linked_account_id,
        "linked_account_type": offer.linked_account_type,
        "linked_account_label": _linked_label_safe(session, offer) if session else "",
        "intake_source": offer.intake_source,
        "intake_status": offer.intake_status,
        "recommendation_status": offer.recommendation_status,
        "confidence": offer.confidence,
        "comparison": comparison,
        "created_at": offer.created_at.isoformat() + "Z",
        "updated_at": offer.updated_at.isoformat() + "Z" if offer.updated_at else None,
    }


def offer_to_research_dict(offer: FinancialOffer) -> dict[str, Any]:
    warnings = offer.warnings.split("|") if offer.warnings else []
    return {
        "provider": offer.provider,
        "product_type": offer.product_type,
        "estimated_apr": offer.estimated_apr,
        "estimated_savings": offer.estimated_savings,
        "monthly_payment_estimate": offer.monthly_payment_estimate,
        "eligibility_notes": offer.offer_name or offer.eligibility_notes,
        "source_url": offer.source_url,
        "confidence": offer.confidence,
        "warnings": warnings,
        "raw_source": "approved_user_intake",
        "math": {"offer_id": offer.id, "apy": offer.apy},
        "intake_offer_id": offer.id,
    }


def update_offer_link(
    session: Session,
    offer_id: str,
    linked_account_type: str,
    linked_account_id: str,
) -> FinancialOffer:
    offer = session.get(FinancialOffer, offer_id)
    if not offer or offer.intake_source != SOURCE_USER:
        raise ValueError("offer not found")
    errors = validate_offer_link(session, offer.product_type, linked_account_type, linked_account_id)
    if errors:
        raise ValueError("; ".join(errors))
    offer.linked_account_type = linked_account_type
    offer.linked_account_id = linked_account_id
    offer.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(offer)
    session.commit()
    session.refresh(offer)
    event_bus.emit(
        Event(
            event_type=EventType.OFFER_LINKED_ACCOUNT_UPDATED.value,
            source="offer.intake",
            payload={
                "offer_id": offer.id,
                "linked_account_type": linked_account_type,
                "linked_account_id": linked_account_id,
            },
        )
    )
    return offer


def _linked_label_safe(session: Session, offer: FinancialOffer) -> str:
    from app.core.offer_recommendations import resolve_linked_account_label

    return resolve_linked_account_label(session, offer)


def assert_pending_excluded_from_offer_list(offers: list[dict[str, Any]], session: Session) -> None:
    pending_ids = {o.id for o in list_intake_offers(session, intake_status=INTAKE_PENDING)}
    for offer in offers:
        oid = offer.get("intake_offer_id") or offer.get("math", {}).get("offer_id")
        if oid and oid in pending_ids:
            raise RuntimeError("Pending user offer would affect recommendations")
