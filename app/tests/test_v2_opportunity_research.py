"""Deterministic D003 research-brief, evidence, and D002 handoff coverage."""

import json
from datetime import timedelta

from sqlmodel import select

from app.core.opportunity_research import (
    build_research_brief,
    build_research_plan,
    correct_soft_rejections,
    mark_stale_evidence,
    provider_yield_scorecard,
    run_opportunity_research,
)
from app.core.v2_opportunities import (
    add_asset,
    add_skill,
    candidate_history,
    candidate_quality,
    decision_pool,
    update_profile,
    validation_bottleneck_summary,
)
from app.db.models import OpportunityCandidate, OpportunityEvidence, OpportunityResearchRun
from app.research.providers.base import (
    DiscoveryClaim,
    OpportunityDiscovery,
    OpportunityDiscoveryResult,
    ResearchProvider,
    ResearchProviderResult,
)
from app.research.providers.fred_opportunity import FredMortgageOpportunityProvider
from app.research.providers.github_issue_demand import GitHubIssueDemandProvider
from app.research.providers.public_market_evidence import PublicMarketEvidenceProvider
from app.research.providers.remote_ok import RemoteOkProvider
from app.research.providers.treasury_rates import TreasuryYieldProvider, _latest_three_month_yield


def _cashflow(*, deployable=1_000, experimental=250, risks=None):
    return {
        "money_state": {
            "deployable_surplus": deployable,
            "experimental_capital": experimental,
            "risk_windows": risks or [],
        }
    }


class _Provider(ResearchProvider):
    provider_id = "replay"

    def __init__(self, discoveries=None, error=None):
        self.discoveries = discoveries or []
        self.error = error
        self.calls = 0

    def fetch(self, _context):
        return ResearchProviderResult(provider_id=self.provider_id, skipped=True)

    def discover(self, _context, _plan):
        self.calls += 1
        return OpportunityDiscoveryResult(
            provider_id=self.provider_id,
            discoveries=self.discoveries,
            error=self.error,
            request_count=1,
        )


def _discovery(
    *,
    title="Reusable template demand",
    net_low=100,
    net_high=200,
    quality="official_primary",
    canonical_identity="",
    required_activities=None,
    candidate_fields=None,
    claim_value="listing exists",
):
    fields = {
        "verification_status": "verified" if quality == "official_primary" else "unverified",
        "freshness_status": "current",
        "expected_net_low": net_low,
        "expected_net_high": net_high,
        "probability_low": 0.5,
        "probability_high": 0.7,
        "scalability": 0.9,
        "repeatability": 0.9,
        "automation_potential": 0.8,
        "reversibility": 0.9,
    }
    fields.update(candidate_fields or {})
    return OpportunityDiscovery(
        title=title,
        category="digital_product",
        summary="Replay evidence only",
        source_url="https://source.example.test/opportunity",
        source_title="Structured replay source",
        source_domain="source.example.test",
        publisher="Replay publisher",
        source_quality=quality,
        external_identity=f"replay:{title.lower().replace(' ', '-')}",
        canonical_identity=canonical_identity,
        required_activities=required_activities or [],
        claims=[
            DiscoveryClaim(
                claim_type="demand_signal",
                value=claim_value,
                source_quality=quality,
                evidence_strength=0.9 if quality == "official_primary" else 0.3,
                freshness_hours=1,
            )
        ],
        candidate_fields=fields,
    )


def test_research_brief_is_sanitized_and_targeted(session):
    update_profile(
        session,
        {
            "max_setup_hours": 3.5,
            "prefers_scalable_income": True,
            "prefers_low_touch": True,
            "notes": "PRIVATE NOTE account 123456 balance 9876.54",
        },
    )
    add_skill(session, {"name": "Python automation", "notes": "private client work"})
    add_asset(session, {"name": "Template library", "notes": "internal location"})
    brief = build_research_brief(session, _cashflow(deployable=9876.54, experimental=123.45))
    encoded = json.dumps(brief)
    assert "9876.54" not in encoded and "123.45" not in encoded
    assert "PRIVATE NOTE" not in encoded and "private client work" not in encoded
    assert "account 123456" not in encoded and "Template library" in encoded
    assert brief["financial_constraints"]["deployable_surplus_band"] == "5000_or_more"
    plan = build_research_plan(brief)
    assert "productization" in plan["selected_themes"]
    assert not any(word in json.dumps(plan).lower() for word in ("rideshare", "delivery", "survey", "data entry"))


def test_empty_profile_reports_limited_personalization_without_fake_skills(session):
    brief = build_research_brief(session, _cashflow())
    plan = build_research_plan(brief, scope="growth")
    assert brief["personalization_limited"] is True
    assert brief["capability"]["skills"] == []
    assert plan["selected_themes"] == []


def test_public_growth_provider_uses_capability_term_and_preserves_unknown_economics(monkeypatch, session):
    add_skill(session, {"name": "Python automation"})

    class Response:
        content = json.dumps(
            {
                "items": [
                    {
                        "id": 7,
                        "html_url": "https://github.com/example/repo/issues/7",
                        "title": "Automation help",
                        "state": "open",
                        "updated_at": "2026-09-09T00:00:00Z",
                        "labels": [{"name": "help wanted"}],
                        "repository_url": "https://api.github.com/repos/example/repo",
                    }
                ]
            }
        ).encode()

        def raise_for_status(self):
            return None

        def json(self):
            return json.loads(self.content)

    captured = {}
    monkeypatch.setattr(
        "app.research.providers.github_issue_demand.httpx.get",
        lambda url, **kwargs: captured.update({"url": url, **kwargs}) or Response(),
    )
    result = run_opportunity_research(
        session, scope="growth", cashflow=_cashflow(), providers=[GitHubIssueDemandProvider()]
    )
    candidate = session.exec(select(OpportunityCandidate)).one()
    assert captured["url"] == "https://api.github.com/search/issues"
    assert "Python automation" in captured["params"]["q"]
    assert result["candidates_created"] == 1
    assert candidate.expected_net_low is None and candidate.expected_net_high is None
    assert "economics_unknown" in json.loads(candidate.rejection_reasons_json)


def test_treasury_xml_parser_uses_latest_official_three_month_observation():
    payload = b'''<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"
      xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
      <entry><content><m:properties>
        <d:NEW_DATE>2026-01-03T00:00:00</d:NEW_DATE><d:BC_3MONTH>4.20</d:BC_3MONTH>
      </m:properties></content></entry>
      <entry><content><m:properties>
        <d:NEW_DATE>2026-01-05T00:00:00</d:NEW_DATE><d:BC_3MONTH>4.10</d:BC_3MONTH>
      </m:properties></content></entry>
    </feed>'''
    assert _latest_three_month_yield(payload) == ("2026-01-05", 4.10)


def test_treasury_provider_preserves_benchmark_without_fabricated_household_value(monkeypatch):
    class Response:
        content = b'''<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"
          xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
          <entry><content><m:properties>
            <d:NEW_DATE>2026-01-05T00:00:00</d:NEW_DATE><d:BC_3MONTH>4.10</d:BC_3MONTH>
          </m:properties></content></entry>
        </feed>'''

        def raise_for_status(self):
            return None

    monkeypatch.setattr("app.research.providers.treasury_rates.httpx.get", lambda *args, **kwargs: Response())
    result = TreasuryYieldProvider().discover(
        {"has_liquid_accounts": True},
        {"selected_themes": ["financial_optimization"], "limits": {"timeout_seconds": 2}},
    )
    assert result.request_count == 1 and len(result.discoveries) == 1
    discovery = result.discoveries[0]
    assert discovery.claims[0].claim_type == "yield"
    assert "expected_net_low" not in discovery.candidate_fields


def test_remote_ok_provider_limits_results_and_keeps_missing_compensation_unknown(monkeypatch):
    class Response:
        content = json.dumps(
            [
                {"legal": "metadata"},
                {
                    "id": "job-1",
                    "slug": "python-automation-job-1",
                    "url": "https://remoteok.com/remote-jobs/job-1",
                    "position": "Python Automation",
                    "company": "Example",
                    "tags": ["python", "automation"],
                    "salary_min": 0,
                    "salary_max": 0,
                    "date": "2026-09-09T00:00:00Z",
                },
            ]
        ).encode()

        def raise_for_status(self):
            return None

        def json(self):
            return json.loads(self.content)

    monkeypatch.setattr("app.research.providers.remote_ok.httpx.get", lambda *args, **kwargs: Response())
    result = RemoteOkProvider().discover(
        {"research_brief": {"capability": {"skills": ["Python"], "assets": []}}},
        {
            "selected_themes": ["capability_matched_growth"],
            "limits": {"timeout_seconds": 2, "max_results_per_query": 3},
        },
    )
    assert result.request_count == 1 and len(result.discoveries) == 1
    discovery = result.discoveries[0]
    assert discovery.external_identity == "remoteok:job-1"
    assert discovery.claims[0].claim_type == "active_listing"
    assert not any(claim.claim_type == "stated_compensation" for claim in discovery.claims)
    assert "expected_net_low" not in discovery.candidate_fields


def test_remote_ok_provider_matches_normalized_multiword_capability_query(monkeypatch):
    class Response:
        content = json.dumps(
            [
                {
                    "id": "job-2",
                    "slug": "workflow-job-2",
                    "url": "https://remoteok.com/remote-jobs/job-2",
                    "position": "Workflow automation specialist",
                    "company": "Example",
                    "tags": ["automation"],
                    "date": "2026-09-09T00:00:00Z",
                }
            ]
        ).encode()

        def raise_for_status(self):
            return None

        def json(self):
            return json.loads(self.content)

    monkeypatch.setattr("app.research.providers.remote_ok.httpx.get", lambda *args, **kwargs: Response())
    result = RemoteOkProvider().discover(
        {
            "research_brief": {"capability": {"skills": [], "assets": []}},
            "capability_query": {"remote_ok_demand": ["Python automation"]},
        },
        {
            "selected_themes": ["capability_matched_growth"],
            "limits": {"timeout_seconds": 2, "max_results_per_query": 3},
        },
    )
    assert len(result.discoveries) == 1



def test_external_discovery_flows_through_d002_and_preserves_claim_provenance(session):
    result = run_opportunity_research(session, cashflow=_cashflow(), providers=[_Provider([_discovery()])])
    candidate = session.exec(select(OpportunityCandidate)).one()
    evidence = session.exec(select(OpportunityEvidence)).one()
    run = session.exec(select(OpportunityResearchRun)).one()
    assert result["status"] == "success"
    assert candidate.source_type == "researched_external"
    assert candidate.lifecycle_status == "qualified"
    assert candidate.qualification_status == "qualified"
    assert evidence.candidate_id == candidate.id
    assert evidence.claim_type == "demand_signal"
    assert evidence.source_quality == "official_primary"
    assert run.request_count == 1 and run.candidates_qualified == 1


def test_unknown_or_weak_external_economics_do_not_become_top_moves(session):
    unknown = _discovery(net_low=None, net_high=None)
    weak = _discovery(title="Weak anecdote", quality="community_or_anecdotal")
    result = run_opportunity_research(session, cashflow=_cashflow(), providers=[_Provider([unknown, weak])])
    candidates = session.exec(select(OpportunityCandidate)).all()
    assert result["candidates_qualified"] == 0
    assert all(row.qualification_status == "pending" for row in candidates)
    assert all(row.lifecycle_status not in {"rejected", "dismissed", "outcome_recorded"} for row in candidates)
    assert all(row.rank_score is None for row in candidates)


def test_cross_run_dedup_keeps_unchanged_claim_evidence_singleton(session):
    provider = _Provider([_discovery(net_low=100, net_high=200)])
    first = run_opportunity_research(session, cashflow=_cashflow(), providers=[provider])
    provider.discoveries = [_discovery(net_low=120, net_high=220)]
    second = run_opportunity_research(session, cashflow=_cashflow(), providers=[provider])
    candidates = session.exec(select(OpportunityCandidate)).all()
    evidence = session.exec(select(OpportunityEvidence)).all()
    assert first["candidates_created"] == 1
    assert second["candidates_merged"] == 1
    assert len(candidates) == 1 and candidates[0].expected_net_low == 120
    assert len(evidence) == 1
    assert second["providers"][0]["evidence_deduped"] == 1


def test_cross_run_material_claim_change_appends_new_evidence(session):
    provider = _Provider([_discovery(claim_value="first factual observation")])
    run_opportunity_research(session, cashflow=_cashflow(), providers=[provider])
    provider.discoveries = [_discovery(claim_value="materially changed observation")]
    run_opportunity_research(session, cashflow=_cashflow(), providers=[provider])
    evidence = session.exec(select(OpportunityEvidence)).all()
    assert len(evidence) == 2


def test_fresh_provider_evidence_is_skipped_until_material_state_changes(session):
    class ProviderA(_Provider):
        provider_id = "fred_mortgage_opportunity"

    provider = ProviderA([_discovery()])
    first = run_opportunity_research(session, cashflow=_cashflow(), providers=[provider])
    second = run_opportunity_research(session, cashflow=_cashflow(), providers=[provider])
    assert first["request_count"] == 1
    assert second["request_count"] == 0
    assert second["status"] == "success"
    assert second["providers"][0]["skipped_reason"] == "SKIPPED_FRESH_EVIDENCE"
    update_profile(session, {"prefers_async": True})
    third = run_opportunity_research(session, cashflow=_cashflow(), providers=[provider])
    assert third["request_count"] == 1


def test_validation_bottleneck_summary_selects_bounded_task(session):
    discovery = _discovery(net_low=None, net_high=None)
    discovery.category = "cash_yield_benchmark"
    run_opportunity_research(session, cashflow=_cashflow(), providers=[_Provider([discovery])])
    summary = validation_bottleneck_summary(session)
    assert summary["counts"]["missing_expected_net"] == 1
    assert summary["next_best_validation_task"]["action"]


def test_capital_violation_is_discovered_but_d002_rejects_it(session):
    expensive = _discovery()
    expensive.candidate_fields["capital_at_risk"] = 500
    result = run_opportunity_research(session, cashflow=_cashflow(experimental=0), providers=[_Provider([expensive])])
    candidate = session.exec(select(OpportunityCandidate)).one()
    assert result["results_examined"] == 1
    assert "capital_at_risk_exceeds_experimental_capital" in json.loads(candidate.rejection_reasons_json)


def test_stale_evidence_rejects_previously_qualified_candidate(session):
    run_opportunity_research(session, cashflow=_cashflow(), providers=[_Provider([_discovery()])])
    evidence = session.exec(select(OpportunityEvidence)).one()
    changed = mark_stale_evidence(session, now=evidence.expires_at + timedelta(seconds=1))
    candidate = session.exec(select(OpportunityCandidate)).one()
    assert changed == 1
    assert evidence.status == "stale"
    assert candidate.freshness_status == "stale"
    assert "external_evidence_not_verified_fresh" in json.loads(candidate.rejection_reasons_json)


def test_provider_failure_isolated_and_total_outage_has_no_fake_candidates(session):
    good = _Provider([_discovery()])
    bad = _Provider(error="timeout")
    partial = run_opportunity_research(session, cashflow=_cashflow(), providers=[bad, good])
    assert partial["status"] == "partial" and partial["candidates_created"] == 1
    assert bad.calls == 2  # configured bounded retry, then isolate the failure
    clean_session = session
    # A fresh run with no discoveries can record failure without manufacturing a candidate.
    total = run_opportunity_research(clean_session, cashflow=_cashflow(), providers=[_Provider(error="offline")])
    assert total["status"] == "failed"
    assert len(clean_session.exec(select(OpportunityCandidate)).all()) == 1


def test_unknown_economics_stays_in_watching_not_history(session):
    add_skill(session, {"name": "Python automation"})
    discovery = _discovery(net_low=None, net_high=None)
    run_opportunity_research(session, cashflow=_cashflow(), providers=[_Provider([discovery])])
    candidate = session.exec(select(OpportunityCandidate)).one()

    assert candidate_quality(candidate) == "NEEDS_ECONOMICS"
    assert candidate in decision_pool(session)
    assert candidate not in candidate_history(session)


def test_owner_refused_activity_is_prefiltered_before_candidate_persistence(session):
    update_profile(session, {"sales_outreach_willingness": False, "physical_labor_willingness": False})
    discovery = _discovery(required_activities=["sales_outreach"])
    result = run_opportunity_research(session, cashflow=_cashflow(), providers=[_Provider([discovery])])
    assert result["candidates_created"] == 0 and result["candidates_prefiltered"] == 1
    assert session.exec(select(OpportunityCandidate)).all() == []


def test_known_one_time_geography_and_payoff_constraints_prefilter(session):
    update_profile(
        session,
        {
            "allows_one_time_income": False,
            "minimum_meaningful_payoff": 500,
        },
    )
    discovery = _discovery(
        candidate_fields={
            "income_type": "one_time",
            "expected_net_high": 100,
            "geographic_eligible": False,
        }
    )
    result = run_opportunity_research(session, cashflow=_cashflow(), providers=[_Provider([discovery])])
    assert result["candidates_created"] == 0 and result["candidates_prefiltered"] == 1
    assert session.exec(select(OpportunityCandidate)).all() == []


def test_cross_source_canonical_identity_merges_evidence_without_false_merge(session):
    class ProviderA(_Provider):
        provider_id = "provider_a"

    class ProviderB(_Provider):
        provider_id = "provider_b"

    first = _discovery(title="Same project", canonical_identity="listing:acme:automation:remote")
    second = _discovery(title="Different wording", canonical_identity="listing:acme:automation:remote")
    run_opportunity_research(session, cashflow=_cashflow(), providers=[ProviderA([first]), ProviderB([second])])
    candidates = session.exec(select(OpportunityCandidate)).all()
    evidence = session.exec(select(OpportunityEvidence)).all()
    assert len(candidates) == 1
    assert {row.provider_id for row in evidence} == {"provider_a", "provider_b"}

    third = _discovery(title="Same project", canonical_identity="listing:other:automation:remote")
    run_opportunity_research(session, cashflow=_cashflow(), providers=[ProviderB([third])])
    assert len(session.exec(select(OpportunityCandidate)).all()) == 2


def test_provider_yield_scorecard_counts_origin_and_corroboration(session):
    class ProviderA(_Provider):
        provider_id = "fred_mortgage_opportunity"

    class ProviderB(_Provider):
        provider_id = "treasury_yield_benchmark"

    discovery = _discovery(canonical_identity="benchmark:shared")
    run_opportunity_research(session, cashflow=_cashflow(), providers=[ProviderA([discovery]), ProviderB([discovery])])
    rows = {row["provider_id"]: row for row in provider_yield_scorecard(session)}
    assert rows["fred_mortgage_opportunity"]["unique_candidates_originated"] == 1
    assert rows["treasury_yield_benchmark"]["candidates_corroborated"] == 1
    assert rows["fred_mortgage_opportunity"]["needs_evidence"] == 0


def test_soft_rejection_correction_is_idempotent(session):
    discovery = _discovery(net_low=None, net_high=None)
    run_opportunity_research(session, cashflow=_cashflow(), providers=[_Provider([discovery])])
    candidate = session.exec(select(OpportunityCandidate)).one()
    candidate.lifecycle_status = "rejected"
    candidate.qualification_status = "rejected"
    candidate.rejection_reasons_json = json.dumps(["economics_unknown"])
    session.add(candidate)
    session.commit()
    evidence = session.exec(select(OpportunityEvidence)).one()
    evidence.provider_id = "github_issue_demand"
    session.add(evidence)
    session.commit()
    first = correct_soft_rejections(session)
    second = correct_soft_rejections(session)
    candidate = session.get(OpportunityCandidate, candidate.id)
    assert first["changed"] == 1 and second["changed"] == 0
    assert candidate.lifecycle_status == "researched"
    assert candidate.qualification_status == "pending"


def test_public_fred_adapter_parses_replayed_csv_without_credentials(monkeypatch):
    class Response:
        content = b"observation_date,MORTGAGE30US\n2026-09-01,6.25\n"

        def raise_for_status(self):
            return None

    monkeypatch.setattr("app.research.providers.fred_opportunity.httpx.get", lambda *_args, **_kwargs: Response())
    result = FredMortgageOpportunityProvider().discover(
        {"has_mortgage": True},
        {"selected_themes": ["financial_optimization"], "limits": {"timeout_seconds": 1}},
    )
    assert result.error is None and result.request_count == 1
    claim = result.discoveries[0].claims[0]
    assert (claim.claim_type, claim.value, claim.published_at) == ("rate", 6.25, "2026-09-01")


def test_public_market_claims_require_claim_specific_facts(monkeypatch):
    class Response:
        def __init__(self, snippet: str):
            self.content = (
                '<a class="result__a" href="https://example.com/offer">Offer</a>'
                f'<div class="result__snippet">{snippet}</div>'
            ).encode()
            self.text = self.content.decode()

        def raise_for_status(self):
            return None

    hypothesis = {"title": "Documentation service", "target_buyer": "small teams", "problem": "docs"}
    monkeypatch.setattr(
        "app.research.providers.public_market_evidence.httpx.get",
        lambda *_args, **_kwargs: Response("A service teams need, with a comparable price of $40."),
    )
    result = PublicMarketEvidenceProvider().discover_hypothesis(hypothesis)
    assert len(result["comparable_price"].discoveries) == 1
    assert len(result["required_cost"].discoveries) == 1
    assert result["comparable_price"].discoveries[0].claims[0].claim_type == "comparable_price"

    monkeypatch.setattr(
        "app.research.providers.public_market_evidence.httpx.get",
        lambda *_args, **_kwargs: Response("A service teams need, with no published pricing."),
    )
    unresolved = PublicMarketEvidenceProvider().discover_hypothesis(hypothesis)
    assert unresolved["comparable_price"].discoveries == []
    assert unresolved["required_cost"].discoveries == []
