"""Small public-web adapter for hypothesis claim evidence.

This is deliberately a bounded source adapter, not a recommendation engine:
it records only the claim being searched and the source text returned by the
public search endpoint. Missing or ambiguous claims remain unresolved.
"""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import quote_plus, urlparse

import httpx

from app.research.providers.base import (
    DiscoveryClaim,
    OpportunityDiscovery,
    OpportunityDiscoveryResult,
    ResearchProvider,
    ResearchProviderResult,
)

SEARCH_URL = "https://html.duckduckgo.com/html/?q="
MAX_RESPONSE_BYTES = 256 * 1024
_RESULT_RE = re.compile(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
_SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>(.*?)</(?:a|div|span)>', re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_MONEY_RE = re.compile(r"\$\s?\d+(?:[,.]\d{3})*(?:\.\d{1,2})?")
_DEMAND_RE = re.compile(
    r"\b(?:need|needs|looking for|seeking|request(?:ed|s)?|help wanted|hire|problem|solution)\b", re.I
)
_COST_RE = re.compile(r"\b(?:cost|price|fee|material|hosting|software|tool|shipping|license)\b", re.I)


def _source_quality(domain: str) -> str:
    domain = domain.casefold()
    if domain.endswith(".gov") or domain.endswith(".edu"):
        return "official_primary"
    if domain.endswith("github.com") or "marketplace" in domain or domain.endswith(".com"):
        return "reputable_secondary"
    return "community_or_anecdotal"


class PublicMarketEvidenceProvider(ResearchProvider):
    provider_id = "public_market_evidence"
    product_types = ["hypothesis_validation"]

    def fetch(self, context: dict[str, Any]) -> ResearchProviderResult:
        """This adapter is discovery-only; no financial offer is returned."""
        return ResearchProviderResult(provider_id=self.provider_id, skipped=True)

    def _search(self, query: str, claim_type: str, *, timeout: float = 4.0) -> OpportunityDiscoveryResult:
        try:
            response = httpx.get(
                SEARCH_URL + quote_plus(query[:180]),
                headers={"User-Agent": "LedgerMind/2.0 bounded-public-evidence"},
                timeout=timeout,
            )
            response.raise_for_status()
            if len(response.content) > MAX_RESPONSE_BYTES:
                return OpportunityDiscoveryResult(
                    provider_id=self.provider_id,
                    error="public evidence response exceeded bounded size",
                    request_count=1,
                )
            body = response.text
        except (httpx.HTTPError, ValueError) as exc:
            return OpportunityDiscoveryResult(provider_id=self.provider_id, error=str(exc), request_count=1)
        links = _RESULT_RE.findall(body)[:3]
        snippets = [_TAG_RE.sub(" ", html.unescape(value)).strip() for value in _SNIPPET_RE.findall(body)[:3]]
        discoveries: list[OpportunityDiscovery] = []
        for index, (url, raw_title) in enumerate(links):
            title = _TAG_RE.sub(" ", html.unescape(raw_title)).strip()[:200]
            snippet = snippets[index] if index < len(snippets) else ""
            money = _MONEY_RE.findall(snippet)
            # Search results are leads, not proof.  Only persist a claim when
            # the destination text contains the claim-specific evidence needed
            # to support it.  In particular, a price/cost query without an
            # actual amount remains unresolved.
            if claim_type in {"comparable_price", "required_cost"} and not money:
                continue
            if claim_type == "required_cost" and not _COST_RE.search(snippet):
                continue
            if claim_type == "buyer_demand" and not _DEMAND_RE.search(snippet):
                continue
            destination = urlparse(html.unescape(url))
            domain = destination.netloc.lower().removeprefix("www.") or "unknown"
            quality = _source_quality(domain)
            value: Any = {"query": query}
            if money:
                value["observed_prices"] = money[:4]
            discoveries.append(OpportunityDiscovery(
                title=title or "Public market evidence",
                category="hypothesis_validation",
                summary=(
                    f"Public source searched for {claim_type.replace('_', ' ')}; "
                    "it is evidence for that claim only."
                ),
                source_url=html.unescape(url),
                source_title=title,
                source_domain=domain,
                publisher=domain,
                source_quality=quality,
                external_identity=f"public-market:{claim_type}:{url}",
                claims=[DiscoveryClaim(
                    claim_type=claim_type,
                    value=value,
                    supporting_snippet=snippet[:500],
                    source_quality=quality,
                    evidence_strength=0.35,
                    published_at=None,
                    freshness_hours=24 * 7,
                )],
            ))
        return OpportunityDiscoveryResult(provider_id=self.provider_id, discoveries=discoveries, request_count=1)

    def discover_hypothesis(self, hypothesis: dict[str, Any]) -> dict[str, OpportunityDiscoveryResult]:
        experiment = hypothesis.get("validation_experiment") or {}
        offer = str(hypothesis.get("title") or "").strip()
        buyer = str(hypothesis.get("target_buyer") or experiment.get("target_buyer") or "").strip()
        problem = str(hypothesis.get("problem") or experiment.get("problem") or "").strip()
        if not offer and not problem:
            return {}
        context = " ".join(part for part in (offer, buyer, problem) if part)
        return {
            "buyer_demand": self._search(f"{context} buyer demand", "buyer_demand"),
            "comparable_price": self._search(f"{context} comparable price", "comparable_price"),
            "required_cost": self._search(f"{context} smallest test materials software cost", "required_cost"),
        }
