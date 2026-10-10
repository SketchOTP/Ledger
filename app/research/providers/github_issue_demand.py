"""Bounded public GitHub issue discovery for capability-matched demand signals.

An open issue is an observed request for work, not an earning claim or a
recommendation to contact anybody.  This adapter uses GitHub's documented
public REST API and only requests a small first page of public results.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx

from app.research.providers.base import (
    DiscoveryClaim,
    OpportunityDiscovery,
    OpportunityDiscoveryResult,
    ResearchProvider,
    ResearchProviderResult,
)

SEARCH_URL = "https://api.github.com/search/issues"
MAX_RESPONSE_BYTES = 128 * 1024


class GitHubIssueDemandProvider(ResearchProvider):
    provider_id = "github_issue_demand"
    product_types = ["capability_matched_growth", "productization"]

    def fetch(self, context: dict[str, Any]) -> ResearchProviderResult:
        return ResearchProviderResult(provider_id=self.provider_id, skipped=True)

    def discover_hypothesis(self, hypothesis: dict[str, Any], *, timeout: float = 5.0) -> OpportunityDiscoveryResult:
        """Search only the concrete hypothesis problem, not its skill names."""
        experiment = hypothesis.get("validation_experiment") or {}
        problem = str(hypothesis.get("problem") or experiment.get("problem") or "").strip()
        buyer = str(hypothesis.get("target_buyer") or experiment.get("target_buyer") or "").strip()
        query = " ".join(part for part in (problem, buyer) if part)[:160]
        if not query:
            return OpportunityDiscoveryResult(
                provider_id="github_hypothesis_demand",
                skipped=True,
                skip_reason="missing hypothesis problem",
            )
        try:
            response = httpx.get(
                SEARCH_URL,
                params={"q": f'"{query}" is:issue is:open', "per_page": 3, "sort": "updated"},
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "LedgerMind/2.0 bounded-hypothesis-research",
                },
                timeout=timeout,
            )
            response.raise_for_status()
            if len(response.content) > MAX_RESPONSE_BYTES:
                return OpportunityDiscoveryResult(
                    provider_id="github_hypothesis_demand",
                    error="GitHub response exceeded bounded size",
                    request_count=1,
                )
            items = response.json().get("items", [])
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return OpportunityDiscoveryResult(provider_id="github_hypothesis_demand", error=str(exc), request_count=1)
        discoveries: list[OpportunityDiscovery] = []
        for item in items[:3]:
            url = str(item.get("html_url") or "")
            title = str(item.get("title") or "Hypothesis problem demand")[:200]
            if not url:
                continue
            updated = str(item.get("updated_at") or "")
            discoveries.append(OpportunityDiscovery(
                title=title,
                category="hypothesis_validation",
                summary="A public issue is a problem-demand signal only; it does not establish price or revenue.",
                source_url=url,
                source_title=title,
                source_domain="github.com",
                publisher="GitHub public issues",
                source_quality="marketplace_or_listing",
                external_identity=f"github_hypothesis:{item.get('id')}",
                claims=[DiscoveryClaim(
                    claim_type="buyer_demand",
                    value={"problem": problem, "buyer": buyer, "issue_id": item.get("id")},
                    supporting_snippet=(
                        f"Public issue matching the hypothesis problem, updated {updated or 'recently'}: {title}"
                    ),
                    source_quality="marketplace_or_listing",
                    evidence_strength=0.55,
                    published_at=updated or None,
                    freshness_hours=24 * 7,
                )],
            ))
        return OpportunityDiscoveryResult(
            provider_id="github_hypothesis_demand", discoveries=discoveries, request_count=1
        )

    def discover(self, context: dict[str, Any], plan: dict[str, Any]) -> OpportunityDiscoveryResult:
        if not {"capability_matched_growth", "productization"} & set(plan.get("selected_themes") or []):
            return OpportunityDiscoveryResult(provider_id=self.provider_id, skipped=True)
        concepts = (context.get("capability_query") or {}).get(self.provider_id)
        if concepts is None:
            capability = (context.get("research_brief") or {}).get("capability") or {}
            concepts = [*(capability.get("skills") or []), *(capability.get("assets") or [])]
        term = next((str(x).strip() for x in concepts if str(x).strip()), "")
        if not term:
            return OpportunityDiscoveryResult(provider_id=self.provider_id, skipped=True, error="no capability terms")
        timeout = float((plan.get("limits") or {}).get("timeout_seconds", 5))
        try:
            response = httpx.get(
                SEARCH_URL,
                params={"q": f'"{term[:80]}" is:issue is:open label:"help wanted"', "per_page": 3, "sort": "updated"},
                headers={"Accept": "application/vnd.github+json", "User-Agent": "LedgerMind/2.0 bounded-research"},
                timeout=timeout,
            )
            response.raise_for_status()
            if len(response.content) > MAX_RESPONSE_BYTES:
                return OpportunityDiscoveryResult(
                    provider_id=self.provider_id, error="GitHub response exceeded bounded size", request_count=1
                )
            items = response.json().get("items", [])
        except httpx.HTTPStatusError as exc:
            return OpportunityDiscoveryResult(
                provider_id=self.provider_id,
                error=f"GitHub HTTP {exc.response.status_code}",
                rate_limited=exc.response.status_code in {403, 429},
                request_count=1,
            )
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            return OpportunityDiscoveryResult(provider_id=self.provider_id, error=str(exc), request_count=1)

        discoveries: list[OpportunityDiscovery] = []
        for item in items[:3]:
            url = str(item.get("html_url") or "")
            title = str(item.get("title") or "Open capability-matched issue")[:200]
            if not url:
                continue
            updated = str(item.get("updated_at") or "")
            snippet = f"Open GitHub issue labelled help wanted, updated {updated or 'at an unspecified time'}: {title}"
            discoveries.append(
                OpportunityDiscovery(
                    title=f"Validate demand for {term}: {title}",
                    category="capability_matched_growth",
                    summary=(
                        "A public open-source issue is a current demand signal only. "
                        "It does not establish a paid budget, acceptance likelihood, or expected income."
                    ),
                    source_url=url,
                    source_title=title,
                    source_domain="github.com",
                    publisher=str((item.get("repository_url") or "github.com").rsplit("/", 1)[-1]),
                    source_quality="marketplace_or_listing",
                    external_identity=f"github_issue:{item.get('id')}",
                    required_activities=["customer_interaction"],
                    claims=[
                        DiscoveryClaim(
                            claim_type="active_listing",
                            value={
                                "state": item.get("state"),
                                "labels": [x.get("name") for x in item.get("labels", [])],
                            },
                            supporting_snippet=snippet,
                            source_quality="marketplace_or_listing",
                            evidence_strength=0.55,
                            published_at=updated or None,
                            freshness_hours=24 * 7,
                        ),
                        DiscoveryClaim(
                            claim_type="skill_requirement",
                            value=term,
                            supporting_snippet=f"Search matched declared capability term: {term}",
                            source_quality="marketplace_or_listing",
                            evidence_strength=0.5,
                            published_at=updated or None,
                            freshness_hours=24 * 7,
                        ),
                    ],
                    candidate_fields={
                        "verification_status": "unverified",
                        "freshness_status": "current",
                        "fetched_at": datetime.now(UTC).replace(tzinfo=None),
                        "matched_skills_json": json.dumps([term]),
                    },
                )
            )
        return OpportunityDiscoveryResult(provider_id=self.provider_id, discoveries=discoveries, request_count=1)
