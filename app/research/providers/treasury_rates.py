"""Bounded Treasury benchmark discovery for cash-yield comparisons.

The Treasury feed is a benchmark, not a household recommendation.  We keep
the observed yield as provenance and leave household APY, principal, fees, and
net value unknown until the owner supplies them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from xml.etree import ElementTree

import httpx

from app.research.providers.base import (
    DiscoveryClaim,
    OpportunityDiscovery,
    OpportunityDiscoveryResult,
    ResearchProvider,
    ResearchProviderResult,
)

TREASURY_XML_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
    "?data=daily_treasury_yield_curve&field_tdr_date_value={year}"
)
# One year of Treasury XML is currently about 270 KiB; keep a finite margin
# while still rejecting unexpectedly large responses.
MAX_RESPONSE_BYTES = 512 * 1024
_ATOM = "http://www.w3.org/2005/Atom"
_DATA = "http://schemas.microsoft.com/ado/2007/08/dataservices"


class TreasuryYieldProvider(ResearchProvider):
    """Official, keyless Treasury yield benchmark with one bounded request."""

    provider_id = "treasury_yield_benchmark"
    product_types = ["cash_yield_benchmark", "idle_cash_optimization"]

    def fetch(self, context: dict[str, Any]) -> ResearchProviderResult:
        return ResearchProviderResult(provider_id=self.provider_id, skipped=True)

    def discover(self, context: dict[str, Any], plan: dict[str, Any]) -> OpportunityDiscoveryResult:
        if "financial_optimization" not in set(plan.get("selected_themes") or []):
            return OpportunityDiscoveryResult(provider_id=self.provider_id, skipped=True)
        if not context.get("has_liquid_accounts"):
            return OpportunityDiscoveryResult(
                provider_id=self.provider_id,
                skipped=True,
                skip_reason="SKIPPED_HOUSEHOLD_PREREQUISITE",
            )
        timeout = float((plan.get("limits") or {}).get("timeout_seconds", 5))
        url = TREASURY_XML_URL.format(year=datetime.now(UTC).year)
        try:
            response = httpx.get(
                url,
                headers={"User-Agent": "LedgerMind/2.0 bounded-research (+local-only)"},
                timeout=timeout,
            )
            response.raise_for_status()
            if len(response.content) > MAX_RESPONSE_BYTES:
                return OpportunityDiscoveryResult(
                    provider_id=self.provider_id,
                    error="Treasury response exceeded bounded size",
                    request_count=1,
                )
            observed_on, yield_value = _latest_three_month_yield(response.content)
        except httpx.HTTPStatusError as exc:
            return OpportunityDiscoveryResult(
                provider_id=self.provider_id,
                error=f"Treasury HTTP {exc.response.status_code}",
                rate_limited=exc.response.status_code == 429,
                request_count=1,
            )
        except (httpx.HTTPError, ElementTree.ParseError, ValueError) as exc:
            return OpportunityDiscoveryResult(provider_id=self.provider_id, error=str(exc), request_count=1)

        claim = DiscoveryClaim(
            claim_type="yield",
            value=yield_value,
            units="percent_annualized",
            supporting_snippet=f"Official Treasury 3-month yield: {yield_value}% on {observed_on}.",
            source_quality="official_primary",
            evidence_strength=0.95,
            published_at=observed_on,
            freshness_hours=48,
        )
        discovery = OpportunityDiscovery(
            title="Compare eligible idle cash with the current Treasury benchmark",
            category="cash_yield_benchmark",
            summary=(
                "A current official Treasury benchmark is available for comparison. "
                "Household APY, eligible principal, fees, and net value remain unknown."
            ),
            source_url=url,
            source_title="Daily Treasury Yield Curve Rates",
            source_domain="home.treasury.gov",
            publisher="U.S. Department of the Treasury",
            source_quality="official_primary",
            external_identity="treasury:daily-yield-curve:3-month",
            canonical_identity="benchmark:treasury:daily-yield-curve:3-month",
            claims=[claim],
            candidate_fields={
                "verification_status": "verified",
                "freshness_status": "current",
                "fetched_at": datetime.now(UTC).replace(tzinfo=None),
                "assumptions_json": '["Owner APY and eligible principal are required before estimating value."]',
            },
        )
        return OpportunityDiscoveryResult(
            provider_id=self.provider_id,
            discoveries=[discovery],
            request_count=1,
        )


def _latest_three_month_yield(payload: bytes) -> tuple[str, float]:
    root = ElementTree.fromstring(payload)
    latest: tuple[str, float] | None = None
    for entry in root.findall(f"{{{_ATOM}}}entry"):
        content = entry.find(f"{{{_ATOM}}}content")
        props = (
            content.find("{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}properties")
            if content is not None
            else None
        )
        if props is None:
            continue
        date_node = props.find(f"{{{_DATA}}}NEW_DATE")
        yield_node = props.find(f"{{{_DATA}}}BC_3MONTH")
        if date_node is None or yield_node is None or not date_node.text or not yield_node.text:
            continue
        try:
            observed_on = date_node.text[:10]
            observed = float(yield_node.text)
        except (TypeError, ValueError):
            continue
        if latest is None or observed_on > latest[0]:
            latest = (observed_on, observed)
    if latest is None:
        raise ValueError("Treasury response contained no usable BC_3MONTH observation")
    return latest
