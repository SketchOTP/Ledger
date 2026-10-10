"""Bounded Remote OK discovery for capability-matched demand signals."""

from __future__ import annotations

import json
import re
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

REMOTE_OK_URL = "https://remoteok.com/api"
# The documented public feed contains a bounded first page plus metadata and
# is currently about 650 KiB.  Do not accept unbounded responses.
MAX_RESPONSE_BYTES = 768 * 1024
_ACTIVITY_TERMS = {
    "customer_interaction": ("customer", "client", "support", "sales", "account manager"),
    "sales_outreach": ("sales", "business development", "outreach", "prospecting"),
    "physical_labor": ("technician", "warehouse", "driver", "installation", "field service"),
}


class RemoteOkProvider(ResearchProvider):
    """Public Remote OK feed; compensation is used only when explicitly stated."""

    provider_id = "remote_ok_demand"
    product_types = ["capability_matched_growth", "productization"]

    def fetch(self, context: dict[str, Any]) -> ResearchProviderResult:
        return ResearchProviderResult(provider_id=self.provider_id, skipped=True)

    def discover(self, context: dict[str, Any], plan: dict[str, Any]) -> OpportunityDiscoveryResult:
        if not {"capability_matched_growth", "productization"} & set(plan.get("selected_themes") or []):
            return OpportunityDiscoveryResult(provider_id=self.provider_id, skipped=True)
        capability = (context.get("research_brief") or {}).get("capability") or {}
        selected = (context.get("capability_query") or {}).get(self.provider_id)
        if selected is None:
            selected = [*(capability.get("skills") or []), *(capability.get("assets") or [])]
        terms = [str(x).strip() for x in selected if str(x).strip()]
        if not terms:
            return OpportunityDiscoveryResult(provider_id=self.provider_id, skipped=True, error="no capability terms")
        timeout = float((plan.get("limits") or {}).get("timeout_seconds", 5))
        try:
            response = httpx.get(
                REMOTE_OK_URL,
                headers={"User-Agent": "LedgerMind/2.0 bounded-research (+local-only)"},
                timeout=timeout,
            )
            response.raise_for_status()
            if len(response.content) > MAX_RESPONSE_BYTES:
                return OpportunityDiscoveryResult(
                    provider_id=self.provider_id,
                    error="Remote OK response exceeded bounded size",
                    request_count=1,
                )
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("Remote OK response was not a list")
        except httpx.HTTPStatusError as exc:
            return OpportunityDiscoveryResult(
                provider_id=self.provider_id,
                error=f"Remote OK HTTP {exc.response.status_code}",
                rate_limited=exc.response.status_code == 429,
                request_count=1,
            )
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return OpportunityDiscoveryResult(provider_id=self.provider_id, error=str(exc), request_count=1)

        discoveries: list[OpportunityDiscovery] = []
        for item in payload:
            if not isinstance(item, dict) or "legal" in item:
                continue
            text = " ".join(
                [str(item.get("position") or ""), *(str(tag) for tag in item.get("tags") or [])]
            )
            normalized_text = _normalize_text(text)
            matched = next(
                (term for term in terms[:3] if _term_matches(term, normalized_text)),
                None,
            )
            if not matched:
                continue
            url = str(item.get("url") or item.get("apply_url") or "")
            if not url:
                continue
            title = str(item.get("position") or "Capability-matched remote listing")[:200]
            activities = _activities_for(text)
            claims = [
                DiscoveryClaim(
                    claim_type="active_listing",
                    value={"id": item.get("id"), "tags": item.get("tags") or []},
                    supporting_snippet=f"Remote OK listing matched declared capability: {matched}",
                    source_quality="marketplace_or_listing",
                    evidence_strength=0.65,
                    published_at=str(item.get("date") or "") or None,
                    freshness_hours=24 * 7,
                ),
                DiscoveryClaim(
                    claim_type="skill_requirement",
                    value=matched,
                    supporting_snippet=f"Listing tags/title matched declared capability: {matched}",
                    source_quality="marketplace_or_listing",
                    evidence_strength=0.6,
                    published_at=str(item.get("date") or "") or None,
                    freshness_hours=24 * 7,
                ),
            ]
            candidate_fields: dict[str, Any] = {
                "verification_status": "unverified",
                "freshness_status": "current",
                "fetched_at": datetime.now(UTC).replace(tzinfo=None),
                "matched_skills_json": json.dumps([matched]),
                "continued_labor_dependence": 1.0,
                "assumptions_json": '["Compensation, acceptance probability, and effort are not inferred."]',
            }
            salary_min, salary_max = _stated_salary(item)
            if salary_min is not None:
                candidate_fields["expected_gross_low"] = salary_min
                candidate_fields["expected_gross_high"] = salary_max
                claims.append(
                    DiscoveryClaim(
                        claim_type="stated_compensation",
                        value={"low": salary_min, "high": salary_max, "period": "source_stated"},
                        units="usd_source_stated",
                        supporting_snippet="Compensation range was explicitly present in the source feed.",
                        source_quality="marketplace_or_listing",
                        evidence_strength=0.7,
                        published_at=str(item.get("date") or "") or None,
                        freshness_hours=24 * 7,
                    )
                )
            discoveries.append(
                OpportunityDiscovery(
                    title=f"Validate demand: {title}",
                    category="capability_matched_growth",
                    summary=(
                        "A public remote listing matches a declared capability. "
                        "It is demand evidence; net income and acceptance probability remain unknown."
                    ),
                    source_url=url,
                    source_title=title,
                    source_domain="remoteok.com",
                    publisher=str(item.get("company") or "Remote OK")[:120],
                    source_quality="marketplace_or_listing",
                    external_identity=f"remoteok:{item.get('id') or item.get('slug') or url}",
                    canonical_identity=_canonical_listing_identity(item),
                    required_activities=activities,
                    claims=claims,
                    candidate_fields=candidate_fields,
                )
            )
            if len(discoveries) >= int((plan.get("limits") or {}).get("max_results_per_query", 3)):
                break
        return OpportunityDiscoveryResult(provider_id=self.provider_id, discoveries=discoveries, request_count=1)


def _activities_for(text: str) -> list[str]:
    return [activity for activity, terms in _ACTIVITY_TERMS.items() if any(term in text for term in terms)]


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _term_matches(term: str, normalized_text: str) -> bool:
    normalized_term = _normalize_text(term)
    if not normalized_term:
        return False
    if normalized_term in normalized_text:
        return True
    text_tokens = set(normalized_text.split())
    tokens = [token for token in normalized_term.split() if len(token) >= 4]
    return bool(tokens) and any(token in text_tokens for token in tokens)


def _canonical_listing_identity(item: dict[str, Any]) -> str:
    """Build a conservative cross-source key; missing employer stays distinct."""
    company = " ".join(str(item.get("company") or "").lower().split())
    role = " ".join(str(item.get("position") or "").lower().split())
    location = " ".join(str(item.get("location") or "remote").lower().split())
    if not company or not role:
        return ""
    return f"listing:{company}:{role}:{location}"


def _stated_salary(item: dict[str, Any]) -> tuple[float | None, float | None]:
    try:
        low = float(item.get("salary_min") or 0)
        high = float(item.get("salary_max") or 0)
    except (TypeError, ValueError):
        return None, None
    if low <= 0 and high <= 0:
        return None, None
    if low <= 0:
        low = high
    if high <= 0:
        high = low
    return low, max(low, high)
