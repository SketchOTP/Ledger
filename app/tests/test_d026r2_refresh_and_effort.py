"""D026R2 dependent-refresh composition and effort semantics guards."""

import json
from datetime import UTC, date, datetime, timedelta

from sqlmodel import select

from app.api.routes_bills import create_bill
from app.api.routes_income import create_source
from app.api.routes_profile import update_account, update_card, update_mortgage
from app.api.schemas import BillCreate, IncomeSourceCreate
from app.core.v2_opportunities import rank_candidate
from app.core.v2_validation import save_fact
from app.db.models import (
    CreditCard,
    LiquidAccount,
    MortgageAccount,
    OpportunityCandidate,
    OpportunityEvidence,
    OpportunityResearchRun,
)


def _cashflow(_session, deployable=10000.0):
    return {
        "money_state": {
            "deployable_surplus": deployable,
            "experimental_capital": deployable,
            "risk_windows": [],
        }
    }


def _patch_cashflow(monkeypatch, deployable=10000.0):
    monkeypatch.setattr("app.core.v2_opportunities.calculate_cashflow", lambda session: _cashflow(session, deployable))
    monkeypatch.setattr("app.core.v2_validation.calculate_cashflow", lambda session: _cashflow(session, deployable))


def _candidate(session, category, identity):
    row = OpportunityCandidate(
        identity_key=identity,
        title=identity,
        category=category,
        source_type="owner_supplied",
        lifecycle_status="researched",
        qualification_status="pending",
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _treasury_evidence(session, candidate, rate=4.0):
    run = OpportunityResearchRun(status="success")
    session.add(run)
    session.commit()
    row = OpportunityEvidence(
        research_run_id=run.id,
        candidate_id=candidate.id,
        provider_id="treasury_yield_benchmark",
        source_url="https://home.treasury.gov/",
        claim_type="yield",
        factual_value_json=json.dumps(rate),
        units="percent_annualized",
        evidence_strength=0.95,
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=1),
    )
    session.add(row)
    session.commit()


def test_account_route_refreshes_existing_cash_candidate(session, monkeypatch):
    _patch_cashflow(monkeypatch)
    account = LiquidAccount(account_name="Savings", account_type="savings", balance=5000, current_apy=1.0)
    session.add(account)
    session.commit()
    candidate = _candidate(session, "cash_yield_benchmark", "account-refresh")
    _treasury_evidence(session, candidate)
    save_fact(session, candidate, fact_type="comparison_account_id", value=account.id, units="account")
    before = candidate.expected_net_low

    update_account(account.id, {"current_apy": 2.0}, session)
    session.refresh(candidate)
    assert candidate.expected_net_low < before
    assert candidate.qualification_status == "qualified"


def test_mortgage_route_refreshes_existing_refinance_candidate(session, monkeypatch):
    _patch_cashflow(monkeypatch)
    mortgage = MortgageAccount(
        lender="Fixture",
        original_balance=100000,
        current_balance=100000,
        interest_rate=7.0,
        monthly_payment=700,
        term_years=30,
        remaining_term_months=240,
        recurrence="none",
    )
    session.add(mortgage)
    session.commit()
    candidate = _candidate(session, "mortgage_refinance", "mortgage-refresh")
    for fact_type, value, units in (
        ("quoted_apr", 5.5, "percent"),
        ("quoted_term_months", 240, "months"),
        ("closing_costs", 1000, "currency"),
    ):
        save_fact(session, candidate, fact_type=fact_type, value=value, units=units)
    before = candidate.expected_net_low

    update_mortgage(
        mortgage.id,
        {
            "lender": "Fixture",
            "original_balance": 100000,
            "current_balance": 90000,
            "interest_rate": 7.0,
            "monthly_payment": 700,
            "term_years": 30,
            "remaining_term_months": 240,
            "recurrence": "none",
        },
        session,
    )
    session.refresh(candidate)
    assert candidate.expected_net_low != before


def test_card_route_refreshes_external_candidate_and_native_sync(session, monkeypatch):
    _patch_cashflow(monkeypatch)
    card = CreditCard(issuer="Fixture", card_name="Card", balance=1000, credit_limit=5000, apr=24, minimum_payment=40)
    session.add(card)
    session.commit()
    candidate = _candidate(session, "credit_card", "card-refresh")
    for fact_type, value, units in (
        ("promotional_apr", 0, "percent"),
        ("promotion_duration_months", 12, "months"),
        ("transfer_fee_fixed", 25, "currency"),
    ):
        save_fact(session, candidate, fact_type=fact_type, value=value, units=units)
    before = candidate.expected_net_low

    update_card(
        card.id,
        {
            "issuer": "Fixture",
            "card_name": "Card",
            "balance": 800,
            "credit_limit": 5000,
            "apr": 24,
            "minimum_payment": 40,
        },
        session,
    )
    session.refresh(candidate)
    assert candidate.expected_net_low != before


def test_effort_semantics_do_not_treat_unknown_as_free(session):
    base = OpportunityCandidate(
        identity_key="known",
        title="known",
        expected_net_low=1000,
        expected_net_high=1000,
        probability_low=1,
        probability_high=1,
        setup_hours=1,
        recurring_hours_per_week=0,
    )
    unknown = OpportunityCandidate(
        identity_key="unknown",
        title="unknown",
        expected_net_low=1000,
        expected_net_high=1000,
        probability_low=1,
        probability_high=1,
    )
    high = OpportunityCandidate(
        identity_key="high",
        title="high",
        expected_net_low=1000,
        expected_net_high=1000,
        probability_low=1,
        probability_high=1,
        setup_hours=20,
        recurring_hours_per_week=8,
    )
    known_result = rank_candidate(base)
    unknown_result = rank_candidate(unknown)
    high_result = rank_candidate(high)
    assert known_result["effort_semantics"] == "known"
    assert unknown_result["effort_semantics"] == "unknown"
    assert unknown_result["effort_hours_4_weeks"] is None
    assert known_result["score"] >= unknown_result["score"] > high_result["score"]


def test_native_debt_uses_explicit_minimal_owner_action(session):
    candidate = OpportunityCandidate(
        identity_key="debt",
        title="Pay extra toward debt",
        category="debt_paydown",
        expected_net_low=1000,
        expected_net_high=1000,
    )
    result = rank_candidate(candidate)
    assert result["effort_semantics"] == "minimal_owner_action"
    assert result["effort_hours_4_weeks"] is None


def test_bill_write_immediately_invalidates_native_move(session, monkeypatch):
    _patch_cashflow(monkeypatch, deployable=600)
    card = CreditCard(issuer="Fixture", card_name="Card", balance=3000, credit_limit=5000, apr=29, minimum_payment=150)
    session.add(card)
    session.commit()
    from app.core.v2_opportunities import sync_household_native_value_moves

    sync_household_native_value_moves(session, _cashflow(session, 600))
    assert session.exec(select(OpportunityCandidate).where(OpportunityCandidate.category == "debt_paydown")).first()

    monkeypatch.setattr("app.api.routes_bills.calculate_cashflow", lambda _session: _cashflow(_session, 0))
    create_bill(BillCreate(name="Future bill", amount=600, due_date=date.today(), recurrence="once"), session)
    native = session.exec(select(OpportunityCandidate).where(OpportunityCandidate.category == "debt_paydown")).first()
    assert native is not None and native.safety_eligible is False


def test_income_write_immediately_creates_native_move_when_capacity_rises(session, monkeypatch):
    _patch_cashflow(monkeypatch, deployable=0)
    card = CreditCard(issuer="Fixture", card_name="Card", balance=3000, credit_limit=5000, apr=29, minimum_payment=150)
    session.add(card)
    session.commit()
    from app.core.v2_opportunities import sync_household_native_value_moves

    sync_household_native_value_moves(session, _cashflow(session, 0))
    assert (
        session.exec(select(OpportunityCandidate).where(OpportunityCandidate.category == "debt_paydown")).first()
        is None
    )

    _patch_cashflow(monkeypatch, deployable=600)
    create_source(
        IncomeSourceCreate(source_name="Job", amount=1000, pay_frequency="monthly", next_pay_date=date.today()),
        session,
    )
    native = session.exec(select(OpportunityCandidate).where(OpportunityCandidate.category == "debt_paydown")).first()
    assert native is not None and native.safety_eligible is True


def test_source_neutral_ranking_follows_supported_economics(session):
    native = OpportunityCandidate(
        identity_key="native",
        title="Native debt",
        category="debt_paydown",
        source_type="deterministic_internal",
        verification_status="verified",
        freshness_status="current",
        expected_net_low=600,
        expected_net_high=600,
        setup_hours=1,
        recurring_hours_per_week=0,
    )
    external = OpportunityCandidate(
        identity_key="external",
        title="External savings",
        category="subscription",
        source_type="researched_external",
        verification_status="verified",
        freshness_status="current",
        expected_net_low=500,
        expected_net_high=500,
        setup_hours=1,
        recurring_hours_per_week=0,
    )
    assert rank_candidate(native)["score"] > rank_candidate(external)["score"]
    native.expected_net_low = native.expected_net_high = 400
    external.expected_net_low = external.expected_net_high = 500
    assert rank_candidate(external)["score"] > rank_candidate(native)["score"]
