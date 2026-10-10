"""D023R1 evidence-to-economics, probability, and validation guards."""

import json
from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_db
from app.core.opportunity_research import mark_stale_evidence
from app.core.v2_opportunities import candidate_quality, rank_candidate
from app.core.v2_validation import save_fact
from app.db.models import (
    CreditCard,
    LiquidAccount,
    MortgageAccount,
    OpportunityCandidate,
    OpportunityEvidence,
    OpportunityResearchRun,
    OpportunityValidationFact,
)
from app.main import app


def _candidate(session: Session, *, category: str, identity: str, **kwargs) -> OpportunityCandidate:
    row = OpportunityCandidate(
        identity_key=identity,
        title=kwargs.pop("title", category),
        category=category,
        source_type=kwargs.pop("source_type", "researched_external"),
        verification_status=kwargs.pop("verification_status", "verified"),
        freshness_status=kwargs.pop("freshness_status", "current"),
        lifecycle_status=kwargs.pop("lifecycle_status", "researched"),
        qualification_status=kwargs.pop("qualification_status", "pending"),
        **kwargs,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _treasury_evidence(session: Session, candidate: OpportunityCandidate, value: float = 4.0, *, expires=None):
    run = OpportunityResearchRun(status="success")
    session.add(run)
    session.commit()
    row = OpportunityEvidence(
        research_run_id=run.id,
        candidate_id=candidate.id,
        provider_id="treasury_yield_benchmark",
        source_url="https://home.treasury.gov/",
        claim_type="yield",
        factual_value_json=json.dumps(value),
        units="percent_annualized",
        evidence_strength=0.95,
        expires_at=expires or datetime.now(UTC).replace(tzinfo=None) + timedelta(days=1),
    )
    session.add(row)
    session.commit()
    return row


def _cashflow(monkeypatch, *, deployable: float = 600.0):
    value = {"money_state": {"deployable_surplus": deployable, "experimental_capital": 10000.0, "risk_windows": []}}
    monkeypatch.setattr("app.core.v2_validation.calculate_cashflow", lambda session: value)
    monkeypatch.setattr("app.core.v2_opportunities.calculate_cashflow", lambda session: value)


def test_treasury_evidence_drives_cash_yield_and_d001_caps_principal(session, monkeypatch):
    _cashflow(monkeypatch, deployable=600)
    account = LiquidAccount(
        account_name="Savings", account_type="savings", balance=5000, current_apy=1.0, apy_as_of=date.today()
    )
    session.add(account)
    session.commit()
    candidate = _candidate(session, category="cash_yield_benchmark", identity="treasury-e2e")
    _treasury_evidence(session, candidate, 4.0)

    result = save_fact(session, candidate, fact_type="comparison_account_id", value=account.id, units="account")
    session.refresh(candidate)

    assert candidate.expected_net_low == 18.0
    assert candidate.required_capital == 600.0
    assert candidate.probability_low is None and candidate.probability_high is None
    assert candidate.qualification_status == "qualified", candidate.rejection_reasons_json
    assert candidate_quality(candidate) == "DECISION_GRADE"
    assert rank_candidate(candidate)["economic_semantics"] == "deterministic_supported_net"
    assert "benchmark spread" in " ".join(json.loads(candidate.assumptions_json))
    assert "comparison_apy" not in {row["fact_type"] for row in result["required_facts"]}


def test_treasury_stale_evidence_and_negative_spread_do_not_rank(session, monkeypatch):
    _cashflow(monkeypatch, deployable=1000)
    account = LiquidAccount(account_name="Savings", account_type="savings", balance=5000, current_apy=5.0)
    session.add(account)
    session.commit()
    stale = _candidate(session, category="cash_yield_benchmark", identity="treasury-stale")
    _treasury_evidence(session, stale, 4.0, expires=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1))
    save_fact(session, stale, fact_type="comparison_account_id", value=account.id, units="account")
    assert stale.expected_net_low is None
    assert candidate_quality(stale) == "NEEDS_ECONOMICS"

    negative = _candidate(session, category="cash_yield_benchmark", identity="treasury-negative")
    _treasury_evidence(session, negative, 4.0)
    save_fact(session, negative, fact_type="comparison_account_id", value=account.id, units="account")
    assert negative.expected_net_high == -10.0
    assert rank_candidate(negative)["score"] == 0.0


def test_stochastic_growth_without_probability_remains_pending(session):
    candidate = _candidate(
        session,
        category="capability_matched_growth",
        identity="growth-no-probability",
        expected_net_low=500,
        expected_net_high=500,
        required_capital=0,
        capital_at_risk=0,
        setup_hours=2,
        recurring_hours_per_week=0,
        time_to_value_days=30,
    )
    from app.core.v2_opportunities import qualify_candidate

    result = qualify_candidate(
        session, candidate, {"money_state": {"deployable_surplus": 1000, "experimental_capital": 1000}}
    )
    assert result["eligible"] is False
    assert "probability_unknown" in result["reasons"]
    assert candidate.qualification_status == "pending"


def test_mortgage_amortization_guards_term_extension(session, monkeypatch):
    _cashflow(monkeypatch, deployable=10000)
    session.add(
        MortgageAccount(
            lender="Fixture",
            original_balance=200000,
            current_balance=200000,
            interest_rate=7.0,
            monthly_payment=1330,
            term_years=30,
            remaining_term_months=240,
        )
    )
    session.commit()
    candidate = _candidate(
        session, category="mortgage_refinance", identity="mortgage-complete", source_type="owner_supplied"
    )
    for fact_type, value, units in (
        ("quoted_apr", 5.5, "percent"),
        ("quoted_term_months", 240, "months"),
        ("closing_costs", 3000, "currency"),
    ):
        save_fact(session, candidate, fact_type=fact_type, value=value, units=units)
    assert candidate.expected_net_low is not None
    assert candidate.qualification_status == "qualified", candidate.rejection_reasons_json

    extension = _candidate(
        session, category="mortgage_refinance", identity="mortgage-extension", source_type="owner_supplied"
    )
    for fact_type, value, units in (
        ("quoted_apr", 6.5, "percent"),
        ("quoted_term_months", 360, "months"),
        ("closing_costs", 1000, "currency"),
    ):
        save_fact(session, extension, fact_type=fact_type, value=value, units=units)
    assert extension.expected_net_low is not None
    assert rank_candidate(extension)["score"] == 0.0


def test_card_percentage_and_fixed_transfer_fees(session, monkeypatch):
    _cashflow(monkeypatch, deployable=10000)
    session.add(
        CreditCard(issuer="Fixture", card_name="Card", balance=1000, credit_limit=5000, apr=24, minimum_payment=40)
    )
    session.commit()
    percent = _candidate(session, category="credit_card", identity="card-percent", source_type="owner_supplied")
    for fact_type, value, units in (
        ("promotional_apr", 0, "percent"),
        ("promotion_duration_months", 12, "months"),
        ("transfer_fee_percent", 3, "percent"),
    ):
        save_fact(session, percent, fact_type=fact_type, value=value, units=units)
    assert percent.expected_net_low == 210.0

    fixed = _candidate(session, category="credit_card", identity="card-fixed", source_type="owner_supplied")
    for fact_type, value, units in (
        ("promotional_apr", 0, "percent"),
        ("promotion_duration_months", 12, "months"),
        ("transfer_fee_fixed", 25, "currency"),
    ):
        save_fact(session, fixed, fact_type=fact_type, value=value, units=units)
    assert fixed.expected_net_low == 215.0


def test_conflict_staleness_versioning_and_auto_requalification(session, monkeypatch):
    _cashflow(monkeypatch, deployable=10000)
    session.add(
        MortgageAccount(
            lender="Fixture",
            original_balance=100000,
            current_balance=100000,
            interest_rate=7,
            monthly_payment=700,
            term_years=30,
            remaining_term_months=240,
        )
    )
    session.commit()
    candidate = _candidate(
        session, category="mortgage_refinance", identity="mortgage-conflict", source_type="researched_external"
    )
    evidence = _treasury_evidence(session, candidate, 5.0)
    evidence.claim_type = "apr"
    session.add(evidence)
    session.commit()
    for fact_type, value, units in (
        ("quoted_apr", 6.0, "percent"),
        ("quoted_term_months", 240, "months"),
        ("closing_costs", 1000, "currency"),
    ):
        save_fact(session, candidate, fact_type=fact_type, value=value, units=units)
    assert "conflicting_validation_facts" in json.loads(candidate.rejection_reasons_json)
    assert candidate.qualification_status == "pending"

    first = session.exec(
        select(OpportunityValidationFact).where(
            OpportunityValidationFact.candidate_id == candidate.id, OpportunityValidationFact.fact_type == "quoted_apr"
        )
    ).one()
    save_fact(session, candidate, fact_type="quoted_apr", value=5.75, units="percent")
    versions = session.exec(
        select(OpportunityValidationFact).where(
            OpportunityValidationFact.candidate_id == candidate.id, OpportunityValidationFact.fact_type == "quoted_apr"
        )
    ).all()
    assert len(versions) == 2 and first.status == "superseded" and versions[-1].supersedes_id == first.id

    for row in versions:
        if row.status == "current":
            row.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
            session.add(row)
    session.commit()
    mark_stale_evidence(session)
    assert candidate.expected_net_low is None
    assert candidate_quality(candidate) == "NEEDS_ECONOMICS"


def test_owner_validation_contract_rejects_probability_and_candidate_mutation(session):
    app.dependency_overrides[get_db] = lambda: session
    client = TestClient(app)
    candidate = _candidate(session, category="cash_yield_benchmark", identity="api-guard")
    try:
        assert (
            client.put(
                f"/api/v2/candidates/{candidate.id}/validation", json={"fact_type": "probability_low", "value": 0.5}
            ).status_code
            == 400
        )
        assert (
            client.put(
                f"/api/v2/candidates/{candidate.id}/validation", json={"fact_type": "expected_net_low", "value": 100}
            ).status_code
            == 400
        )
    finally:
        app.dependency_overrides.clear()


def test_liquid_account_api_accepts_nullable_apy_and_rejects_invalid_rate(session):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    api_session = Session(engine)
    app.dependency_overrides[get_db] = lambda: api_session
    client = TestClient(app)
    try:
        created = client.post(
            "/api/profile/accounts",
            json={
                "account_name": "Savings",
                "account_type": "savings",
                "balance": 100,
                "current_apy": 3.25,
                "apy_as_of": "2026-09-10",
            },
        )
        assert created.status_code == 200 and created.json()["current_apy"] == 3.25, created.text
        assert (
            client.post(
                "/api/profile/accounts",
                json={"account_name": "Bad", "account_type": "savings", "balance": 100, "current_apy": 101},
            ).status_code
            == 400
        )
    finally:
        app.dependency_overrides.clear()
        api_session.close()
