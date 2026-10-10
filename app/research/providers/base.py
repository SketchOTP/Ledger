"""Research provider base — ingestion with provenance, no fabricated rates."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class ResearchOffer:
    provider: str
    product_type: str
    estimated_apr: float | None = None
    estimated_savings: float = 0.0
    monthly_payment_estimate: float | None = None
    eligibility_notes: str = ""
    source_url: str = ""
    fetched_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"))
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    raw_source: str = ""
    math: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "product_type": self.product_type,
            "estimated_apr": self.estimated_apr,
            "estimated_savings": self.estimated_savings,
            "monthly_payment_estimate": self.monthly_payment_estimate,
            "eligibility_notes": self.eligibility_notes,
            "source_url": self.source_url,
            "fetched_at": self.fetched_at,
            "confidence": self.confidence,
            "warnings": self.warnings,
            "raw_source": self.raw_source,
            "math": self.math,
        }


@dataclass
class ResearchProviderResult:
    provider_id: str
    offers: list[ResearchOffer] = field(default_factory=list)
    error: str | None = None
    skipped: bool = False


@dataclass
class DiscoveryClaim:
    """One externally observed fact; hypotheses never become claims here."""

    claim_type: str
    value: Any
    units: str = ""
    supporting_snippet: str = ""
    source_quality: str = "unknown"
    evidence_strength: float = 0.0
    published_at: str | None = None
    freshness_hours: int = 168


@dataclass
class OpportunityDiscovery:
    """A provider result before D002 candidate normalization and qualification."""

    title: str
    category: str
    summary: str
    source_url: str
    source_title: str
    source_domain: str
    publisher: str
    claims: list[DiscoveryClaim] = field(default_factory=list)
    source_quality: str = "unknown"
    external_identity: str = ""
    canonical_identity: str = ""
    required_activities: list[str] = field(default_factory=list)
    candidate_fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class OpportunityDiscoveryResult:
    provider_id: str
    discoveries: list[OpportunityDiscovery] = field(default_factory=list)
    error: str | None = None
    skipped: bool = False
    skip_reason: str | None = None
    rate_limited: bool = False
    request_count: int = 0


class ResearchProvider(ABC):
    provider_id: str = "base"
    product_types: list[str] = []

    @abstractmethod
    def fetch(self, context: dict[str, Any]) -> ResearchProviderResult:
        """Fetch offers from a real source. Never invent rates."""
        pass

    def discover(self, context: dict[str, Any], plan: dict[str, Any]) -> OpportunityDiscoveryResult:
        """Optionally perform bounded opportunity discovery from real sources.

        Existing benchmark providers remain fetch-only; D003 providers implement
        this additive hook and cannot return qualified recommendations.
        """
        return OpportunityDiscoveryResult(provider_id=self.provider_id, skipped=True)
