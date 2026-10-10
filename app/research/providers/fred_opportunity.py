"""Bounded, no-key FRED discovery adapter for mortgage benchmark evidence."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from io import StringIO
from typing import Any

import httpx

from app.research.providers.base import (
    DiscoveryClaim,
    OpportunityDiscovery,
    OpportunityDiscoveryResult,
    ResearchProvider,
    ResearchProviderResult,
)

FRED_GRAPH_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=MORTGAGE30US"
FRED_SERIES_URL = "https://fred.stlouisfed.org/series/MORTGAGE30US"
MAX_RESPONSE_BYTES = 64 * 1024


class FredMortgageOpportunityProvider(ResearchProvider):
    """Public FRED CSV is a real source, not a general-purpose web search."""

    provider_id = "fred_mortgage_opportunity"
    product_types = ["mortgage_refinance"]

    def fetch(self, context: dict[str, Any]) -> ResearchProviderResult:
        return ResearchProviderResult(provider_id=self.provider_id, skipped=True)

    def discover(self, context: dict[str, Any], plan: dict[str, Any]) -> OpportunityDiscoveryResult:
        themes = set(plan.get("selected_themes") or [])
        if "financial_optimization" not in themes:
            return OpportunityDiscoveryResult(provider_id=self.provider_id, skipped=True)
        if not bool(context.get("has_mortgage")):
            return OpportunityDiscoveryResult(
                provider_id=self.provider_id,
                skipped=True,
            )
        timeout = float((plan.get("limits") or {}).get("timeout_seconds", 5))
        try:
            response = httpx.get(
                FRED_GRAPH_URL,
                headers={"User-Agent": "LedgerMind/2.0 bounded-research (+local-only)"},
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.content
            if len(payload) > MAX_RESPONSE_BYTES:
                return OpportunityDiscoveryResult(
                    provider_id=self.provider_id,
                    error="FRED response exceeded bounded size",
                    request_count=1,
                )
            observed_on, rate = _latest_observation(payload.decode("utf-8", errors="replace"))
        except httpx.HTTPStatusError as exc:
            return OpportunityDiscoveryResult(
                provider_id=self.provider_id,
                error=f"FRED HTTP {exc.response.status_code}",
                rate_limited=exc.response.status_code == 429,
                request_count=1,
            )
        except (httpx.HTTPError, ValueError) as exc:
            return OpportunityDiscoveryResult(provider_id=self.provider_id, error=str(exc), request_count=1)

        snippet = f"MORTGAGE30US latest published observation: {rate}% on {observed_on}."
        claim = DiscoveryClaim(
            claim_type="rate",
            value=rate,
            units="percent",
            supporting_snippet=snippet,
            source_quality="official_primary",
            evidence_strength=0.9,
            published_at=observed_on,
            freshness_hours=24 * 14,
        )
        discovery = OpportunityDiscovery(
            title="Review mortgage refinance against current FRED benchmark",
            category="mortgage_refinance",
            summary=(
                "A current public mortgage benchmark may justify a comparison; "
                "household-specific refinance economics are not inferred."
            ),
            source_url=FRED_SERIES_URL,
            source_title="30-Year Fixed Rate Mortgage Average (MORTGAGE30US)",
            source_domain="fred.stlouisfed.org",
            publisher="Federal Reserve Bank of St. Louis",
            source_quality="official_primary",
            external_identity="fred:mortgage30us:refinance-review",
            canonical_identity="benchmark:mortgage30us:refinance-review",
            claims=[claim],
            candidate_fields={
                "verification_status": "verified",
                "freshness_status": "current",
                "fetched_at": datetime.now(UTC).replace(tzinfo=None),
            },
        )
        return OpportunityDiscoveryResult(
            provider_id=self.provider_id,
            discoveries=[discovery],
            request_count=1,
        )


def _latest_observation(raw: str) -> tuple[str, float]:
    rows = csv.DictReader(StringIO(raw))
    latest: tuple[str, float] | None = None
    for row in rows:
        date = str(row.get("observation_date") or row.get("DATE") or "").strip()
        value = str(row.get("MORTGAGE30US") or "").strip()
        if not date or value in {"", "."}:
            continue
        latest = (date, float(value))
    if latest is None:
        raise ValueError("FRED response contained no usable MORTGAGE30US observation")
    return latest
