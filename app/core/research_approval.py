"""Human approval gate — only approved curated data may affect CFO recommendations."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from app.db.models import ResearchIngestionFinding
from app.events.bus import event_bus
from app.events.types import Event, EventType
from app.research.ingestion.types import (
    MANUAL_REVIEW_SOURCE_TYPES,
    STALE_AFTER_DAYS,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    TRUSTED_AUTO_APPROVE_SOURCE_TYPES,
)
from app.research.providers.base import ResearchOffer
from app.research.providers.curated_rates import _load_curated, curated_path


def flag_stale_findings(session: Session) -> int:
    """Mark pending findings older than STALE_AFTER_DAYS as stale."""
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=STALE_AFTER_DAYS)
    count = 0
    for finding in session.exec(select(ResearchIngestionFinding)).all():
        if finding.status != STATUS_PENDING:
            continue
        if finding.fetched_at < cutoff and not finding.is_stale:
            finding.is_stale = True
            finding.updated_at = datetime.now(UTC).replace(tzinfo=None)
            session.add(finding)
            count += 1
    if count:
        session.commit()
    return count


def approve_finding(
    session: Session,
    finding_id: str,
    *,
    reviewer_notes: str = "",
) -> ResearchIngestionFinding:
    """Approve pending finding and merge into curated_rates.json."""
    finding = session.get(ResearchIngestionFinding, finding_id)
    if not finding:
        raise ValueError("finding not found")
    if finding.status != STATUS_PENDING:
        raise ValueError(f"cannot approve finding in status {finding.status}")

    normalized = json.loads(finding.finding_json)
    _merge_into_curated(finding.source_type, normalized)

    finding.status = STATUS_APPROVED
    finding.reviewed_at = datetime.now(UTC).replace(tzinfo=None)
    finding.reviewer_notes = reviewer_notes
    finding.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(finding)
    session.commit()

    event_bus.emit(
        Event(
            event_type=EventType.RESEARCH_FINDING_APPROVED.value,
            source="research.approval",
            payload={"finding_id": finding.id, "source_type": finding.source_type},
        )
    )
    event_bus.emit(
        Event(
            event_type=EventType.RESEARCH_CURATED_DATA_UPDATED.value,
            source="research.approval",
            payload={"source_type": finding.source_type, "action": "approved"},
        )
    )
    return finding


def reject_finding(
    session: Session,
    finding_id: str,
    *,
    reviewer_notes: str = "",
) -> ResearchIngestionFinding:
    """Reject pending finding — does not update curated data."""
    finding = session.get(ResearchIngestionFinding, finding_id)
    if not finding:
        raise ValueError("finding not found")
    if finding.status != STATUS_PENDING:
        raise ValueError(f"cannot reject finding in status {finding.status}")

    finding.status = STATUS_REJECTED
    finding.reviewed_at = datetime.now(UTC).replace(tzinfo=None)
    finding.reviewer_notes = reviewer_notes
    finding.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(finding)
    session.commit()

    event_bus.emit(
        Event(
            event_type=EventType.RESEARCH_FINDING_REJECTED.value,
            source="research.approval",
            payload={"finding_id": finding.id, "source_type": finding.source_type},
        )
    )
    return finding


def get_pending_findings(session: Session) -> list[ResearchIngestionFinding]:
    return [
        f
        for f in session.exec(select(ResearchIngestionFinding)).all()
        if f.status == STATUS_PENDING
    ]


# Backward-compatible alias for API routes and health payloads.
SAFE_BENCHMARK_SOURCE_TYPES = TRUSTED_AUTO_APPROVE_SOURCE_TYPES


def requires_manual_review(source_type: str) -> bool:
    return source_type in MANUAL_REVIEW_SOURCE_TYPES


def approve_trusted_pending_findings(session: Session) -> dict[str, Any]:
    """Approve pending findings from trusted government/API sources that are not stale."""
    approved: list[str] = []
    skipped: list[dict[str, str]] = []
    for finding in get_pending_findings(session):
        if finding.is_stale:
            skipped.append({"id": finding.id, "reason": "stale"})
            continue
        if finding.source_type not in TRUSTED_AUTO_APPROVE_SOURCE_TYPES:
            skipped.append({"id": finding.id, "reason": "requires_manual_review"})
            continue
        try:
            approve_finding(session, finding.id, reviewer_notes="auto-trusted-source")
            approved.append(finding.id)
        except ValueError as exc:
            skipped.append({"id": finding.id, "reason": str(exc)})
    return {"approved_count": len(approved), "approved_ids": approved, "skipped": skipped}


def approve_safe_pending_findings(session: Session) -> dict[str, Any]:
    """Backward-compatible alias — see approve_trusted_pending_findings."""
    return approve_trusted_pending_findings(session)


def has_pending_for_source(session: Session, source_type: str) -> bool:
    return any(
        f.source_type == source_type and f.status == STATUS_PENDING
        for f in session.exec(select(ResearchIngestionFinding)).all()
    )


def _merge_into_curated(source_type: str, normalized: dict[str, Any]) -> None:
    """Write approved normalized finding into curated_rates.json."""
    data = _load_curated() or {"_meta": {"description": "Approved curated rates"}}
    payload = normalized.get("payload") or {}
    as_of = normalized.get("as_of")
    source_url = normalized.get("source_url", "")
    source_label = normalized.get("source_label", "approved ingestion")

    if source_type == "mortgage_rates":
        data["mortgage_rates"] = {
            "rate_percent": normalized.get("primary_value") or payload.get("rate_percent"),
            "rate_30yr": payload.get("rate_30yr") or payload.get("rate_percent"),
            "rate_15yr": payload.get("rate_15yr"),
            "as_of": as_of,
            "source_url": source_url,
            "source_label": source_label,
            "confidence": 0.85,
        }
    elif source_type == "fred_macro_rates":
        data["fred_macro"] = {
            "prime_rate": payload.get("prime_rate"),
            "fed_funds_rate": payload.get("fed_funds_rate"),
            "treasury_10y": payload.get("treasury_10y"),
            "as_of": as_of,
            "source_url": source_url,
            "source_label": source_label,
            "confidence": 0.85,
        }
    elif source_type == "hysa_rates":
        entry = {
            "provider": payload.get("provider", "Approved HYSA"),
            "apy": normalized.get("primary_value") or payload.get("apy"),
            "as_of": as_of,
            "source_url": source_url,
            "source_label": source_label,
            "confidence": 0.8,
            "warnings": ["Confirmed via research approval"],
        }
        data["hysa"] = [entry]
    elif source_type in ("credit_card_balance_transfer", "credit_card_benchmark"):
        apr = normalized.get("primary_value") or payload.get(
            "typical_promo_apr_percent"
        ) or payload.get("benchmark_apr_percent")
        data["balance_transfer_benchmark"] = {
            "typical_promo_apr_percent": apr,
            "typical_transfer_fee_percent": payload.get("typical_transfer_fee_percent", 3),
            "source_url": source_url,
            "source_label": source_label,
            "confidence": 0.75,
        }
        data["credit_card_benchmark"] = {
            "benchmark_apr_percent": apr,
            "as_of": as_of,
            "source_url": source_url,
            "source_label": source_label,
            "confidence": 0.82,
        }
    elif source_type == "personal_loan_rates":
        data["personal_loan_rates"] = {
            "typical_apr_percent": normalized.get("primary_value")
            or payload.get("typical_apr_percent"),
            "as_of": as_of,
            "source_url": source_url,
            "source_label": source_label,
            "confidence": 0.82,
        }
    elif source_type == "auto_loan_rates":
        data["auto_loan_rates"] = {
            "typical_apr_percent": normalized.get("primary_value")
            or payload.get("typical_apr_percent"),
            "as_of": as_of,
            "source_url": source_url,
            "source_label": source_label,
            "confidence": 0.82,
        }
    elif source_type == "insurance_median":
        data["insurance_median_premium_monthly"] = {
            "auto": normalized.get("primary_value") or payload.get("auto"),
            "as_of": as_of,
            "source_url": source_url,
            "source_label": source_label,
            "confidence": 0.65,
        }
    elif source_type == "grocery_prices":
        data["grocery_prices"] = {
            "basket_median_price": normalized.get("primary_value")
            or payload.get("basket_median_price"),
            "items": payload.get("items", []),
            "as_of": as_of,
            "source_url": source_url,
            "source_label": source_label,
            "confidence": 0.6,
        }
    elif source_type == "food_cpi_trend":
        data["food_cpi"] = {
            "index_value": normalized.get("primary_value") or payload.get("index_value"),
            "as_of": as_of,
            "source_url": source_url,
            "source_label": source_label,
            "confidence": 0.8,
            "warnings": payload.get("warnings", ["Index trend — not shelf prices"]),
        }
    elif source_type == "utility_cpi_trend":
        data["utility_cpi"] = {
            "index_value": normalized.get("primary_value") or payload.get("index_value"),
            "as_of": as_of,
            "source_url": source_url,
            "source_label": source_label,
            "confidence": 0.8,
        }
    elif source_type == "bls_cpi_trend":
        data["bls_cpi"] = {
            "food_index_value": payload.get("food_index_value"),
            "electricity_index_value": payload.get("electricity_index_value"),
            "index_value": normalized.get("primary_value"),
            "yoy_change_percent": payload.get("yoy_change_percent"),
            "as_of": as_of,
            "source_url": source_url,
            "source_label": source_label,
            "confidence": 0.78,
        }
    elif source_type == "eia_utility_benchmark":
        data["utility_benchmarks"] = {
            "state": payload.get("state", "US"),
            "electricity_cents_per_kwh": normalized.get("primary_value")
            or payload.get("electricity_cents_per_kwh"),
            "as_of": as_of,
            "source_url": source_url,
            "source_label": source_label,
            "confidence": float(payload.get("confidence", 0.82)),
        }
    elif source_type == "usda_food_outlook":
        data["usda_food_outlook"] = {
            "forecast_yoy_percent": normalized.get("primary_value")
            or payload.get("forecast_yoy_percent"),
            "as_of": as_of,
            "source_url": source_url,
            "source_label": source_label,
            "confidence": 0.7,
            "warnings": payload.get("warnings", ["Forecast context only"]),
        }

    path = curated_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def build_offers_from_approved_curated(context: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Build research offers only from curated_rates.json (approved data).
    Pending ingestion findings are never used here.
    """
    from app.research.providers.curated_rates import CuratedCreditBenchmarkProvider, CuratedHysaProvider
    from app.research.providers.insurance_benchmark import InsuranceBenchmarkProvider

    offers: list[dict[str, Any]] = []
    for provider in (
        CuratedHysaProvider(),
        CuratedCreditBenchmarkProvider(),
        InsuranceBenchmarkProvider(),
    ):
        result = provider.fetch(context)
        for offer in result.offers:
            offers.append(offer.to_dict())

    data = _load_curated() or {}
    mr = data.get("mortgage_rates") or {}
    rate = mr.get("rate_percent")
    if rate is not None:
        for m in context.get("raw_mortgages") or []:
            current = float(m.get("interest_rate", 0))
            offers.append(
                ResearchOffer(
                    provider="Approved curated mortgage rate",
                    product_type="mortgage_refinance",
                    estimated_apr=float(rate),
                    source_url=mr.get("source_url", ""),
                    confidence=float(mr.get("confidence", 0.85)),
                    raw_source=mr.get("source_label", "curated mortgage_rates"),
                    math={"market_rate": rate, "your_rate": current, "as_of": mr.get("as_of")},
                ).to_dict()
            )
        if not context.get("raw_mortgages"):
            offers.append(
                ResearchOffer(
                    provider="Approved curated mortgage rate",
                    product_type="mortgage_rate",
                    estimated_apr=float(rate),
                    source_url=mr.get("source_url", ""),
                    confidence=float(mr.get("confidence", 0.85)),
                    raw_source=mr.get("source_label", ""),
                    math={"market_rate": rate, "as_of": mr.get("as_of")},
                ).to_dict()
            )

    pl = data.get("personal_loan_rates") or {}
    if pl.get("typical_apr_percent") is not None:
        offers.append(
            ResearchOffer(
                provider=pl.get("source_label", "personal loan benchmark"),
                product_type="personal_loan",
                estimated_apr=float(pl["typical_apr_percent"]),
                source_url=pl.get("source_url", ""),
                confidence=float(pl.get("confidence", 0.7)),
                raw_source="approved personal_loan_rates",
            ).to_dict()
        )

    al = data.get("auto_loan_rates") or {}
    if al.get("typical_apr_percent") is not None:
        offers.append(
            ResearchOffer(
                provider=al.get("source_label", "auto loan benchmark"),
                product_type="auto_loan",
                estimated_apr=float(al["typical_apr_percent"]),
                source_url=al.get("source_url", ""),
                confidence=float(al.get("confidence", 0.82)),
                raw_source="approved auto_loan_rates",
                warnings=["National benchmark — not a prequalification offer"],
            ).to_dict()
        )

    cc = data.get("credit_card_benchmark") or {}
    if cc.get("benchmark_apr_percent") is not None:
        offers.append(
            ResearchOffer(
                provider=cc.get("source_label", "credit card APR benchmark"),
                product_type="credit_card_benchmark",
                estimated_apr=float(cc["benchmark_apr_percent"]),
                source_url=cc.get("source_url", ""),
                confidence=float(cc.get("confidence", 0.82)),
                raw_source="approved credit_card_benchmark",
                warnings=["Market average APR — not a specific card offer"],
            ).to_dict()
        )

    return offers


def get_research_offers_for_recommendations(
    session: Session,
    context: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Offers and provenance for refresh cycle.
    Excludes any pending ingestion data — only approved curated file + providers.
    """
    flag_stale_findings(session)
    pending = get_pending_findings(session)
    from app.core.offer_intake import approved_user_offers_as_research_dicts

    offers = build_offers_from_approved_curated(context)
    user_offers = approved_user_offers_as_research_dicts(session)
    offers = offers + user_offers
    provenance = [
        {
            "provider_id": "approved_curated",
            "offer_count": len(offers),
            "pending_ingestion_count": len(pending),
            "user_intake_approved_count": len(user_offers),
            "excluded_pending": True,
        }
    ]
    return offers, provenance


_PENDING_APR_COLLISION_SOURCE_TYPES = frozenset(
    {
        "mortgage_rates",
        "fred_macro_rates",
        "hysa_rates",
        "credit_card_balance_transfer",
        "credit_card_benchmark",
        "personal_loan_rates",
        "auto_loan_rates",
        "insurance_median",
        "grocery_prices",
        "food_cpi_trend",
        "utility_cpi_trend",
        "bls_cpi_trend",
        "eia_utility_benchmark",
        "usda_food_outlook",
    }
)


def _offer_from_approved_source(offer: dict[str, Any]) -> bool:
    """True when the offer is explicitly from approved curated or user-intake data."""
    raw = (offer.get("raw_source") or "").lower()
    if raw.startswith("approved"):
        return True
    if "curated" in raw:
        return True
    if offer.get("intake_status") == "approved":
        return True
    return False


def exclude_offers_with_pending_apr_collisions(
    session: Session,
    offers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Drop unapproved-sourced offers whose APR matches a pending ingestion finding.

    Approved curated/user offers may legitimately share APR values with pending
    findings; those are kept. Pending user-intake offers still raise via
    assert_pending_excluded_from_offer_list.
    """
    from app.core.offer_intake import assert_pending_excluded_from_offer_list

    assert_pending_excluded_from_offer_list(offers, session)
    pending = get_pending_findings(session)
    if not pending:
        return offers

    filtered: list[dict[str, Any]] = []
    for offer in offers:
        if _offer_from_approved_source(offer):
            filtered.append(offer)
            continue
        apr = offer.get("estimated_apr")
        if apr is None:
            filtered.append(offer)
            continue
        collision = False
        for finding in pending:
            if finding.primary_value is None:
                continue
            if finding.source_type not in _PENDING_APR_COLLISION_SOURCE_TYPES:
                continue
            if abs(float(apr) - finding.primary_value) < 1e-9:
                collision = True
                break
        if not collision:
            filtered.append(offer)
    return filtered


def assert_no_pending_in_offers(
    session: Session,
    offers: list[dict[str, Any]],
) -> None:
    """Backward-compatible guard — delegates to collision filter + intake check."""
    exclude_offers_with_pending_apr_collisions(session, offers)
