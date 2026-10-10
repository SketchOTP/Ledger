"""D026R1 native-value safety, target, horizon, and end-to-end guards."""

import json

from sqlmodel import select

from app.api.routes_v2_opportunities import today as today_route
from app.core.financial_snapshot import build_financial_snapshot
from app.core.opportunity_engine import _debt_amortization, detect_opportunities
from app.core.v2_opportunities import sync_household_native_value_moves, top_moves
from app.core.v2_today import build_today
from app.db.models import CreditCard, Debt, OpportunityCandidate


def _safe_cashflow(amount: float) -> dict:
    return {
        "risk_level": "safe" if amount else "tight",
        "warnings": [],
        "horizons": [],
        "money_state": {"deployable_surplus": amount, "experimental_capital": amount, "risk_windows": []},
    }


def _patch_cashflow(monkeypatch, amount: float):
    cf = _safe_cashflow(amount)
    monkeypatch.setattr("app.core.v2_opportunities.calculate_cashflow", lambda _session: cf)
    monkeypatch.setattr("app.core.v2_validation.calculate_cashflow", lambda _session: cf)
    monkeypatch.setattr("app.core.v2_today.calculate_cashflow", lambda _session: cf)
    return cf


def test_debt_horizons_are_simulated_not_prorated():
    baseline = _debt_amortization(12000, 29, 360)
    accelerated = _debt_amortization(12000, 29, 360, extra=1000)
    savings = {
        day: round(baseline["interest_by_days"][day] - accelerated["interest_by_days"][day], 2)
        for day in (30, 60, 90, 180, 365)
    }
    assert 0 < savings[30] < savings[365] < baseline["lifetime_interest"] - accelerated["lifetime_interest"]
    assert savings[90] != round(savings[365] * 90 / 365, 2)


def test_mixed_card_and_loan_produce_one_global_target(session):
    card = CreditCard(issuer="CardCo", card_name="Card", balance=3000, credit_limit=5000, apr=22, minimum_payment=120)
    loan = Debt(creditor="LoanCo", balance=9000, apr=31, minimum_payment=300, debt_type="loan")
    session.add_all([card, loan])
    session.commit()
    snapshot = build_financial_snapshot(session).model_copy(
        update={"money_state": _safe_cashflow(600)["money_state"], "cashflow": _safe_cashflow(600)}
    )
    findings = [row for row in detect_opportunities(snapshot, session) if row["category"] == "debt_paydown"]
    assert len(findings) == 1
    assert findings[0]["math"]["debt_id"] == loan.id


def test_native_move_is_invalidated_when_capacity_falls(session, monkeypatch):
    _patch_cashflow(monkeypatch, 600)
    card = CreditCard(issuer="CardCo", card_name="Card", balance=3000, credit_limit=5000, apr=29, minimum_payment=150)
    session.add(card)
    session.commit()
    assert top_moves(session)
    candidate = session.exec(select(OpportunityCandidate).where(OpportunityCandidate.category == "debt_paydown")).one()
    _patch_cashflow(monkeypatch, 0)
    assert top_moves(session) == []
    session.refresh(candidate)
    assert candidate.safety_eligible is False
    assert candidate.expected_net_low is None


def test_native_move_is_created_when_capacity_rises(session, monkeypatch):
    card = CreditCard(issuer="CardCo", card_name="Card", balance=3000, credit_limit=5000, apr=29, minimum_payment=150)
    session.add(card)
    session.commit()
    _patch_cashflow(monkeypatch, 0)
    assert sync_household_native_value_moves(session)["findings"] == 0
    _patch_cashflow(monkeypatch, 600)
    assert sync_household_native_value_moves(session)["findings"] == 1
    assert top_moves(session)


def test_native_target_refreshes_and_does_not_call_external_research(session, monkeypatch):
    _patch_cashflow(monkeypatch, 600)
    card = CreditCard(issuer="CardCo", card_name="Card", balance=3000, credit_limit=5000, apr=29, minimum_payment=150)
    session.add(card)
    session.commit()
    sync_household_native_value_moves(session)
    assert top_moves(session)


def test_top_moves_flow_into_today_without_external_provider(session, monkeypatch):
    _patch_cashflow(monkeypatch, 700)
    session.add(
        CreditCard(
            issuer="CardCo", card_name="Card", balance=3500, credit_limit=5000, apr=28, minimum_payment=160
        )
    )
    session.commit()
    moves = top_moves(session)
    today = build_today(session)
    api_today = today_route(session)
    assert moves and today["top_moves"]
    assert today["top_moves"][0]["category"] == "debt_paydown"
    assert api_today["top_moves"][0]["category"] == "debt_paydown"


def test_native_evidence_contains_real_horizon_values(session, monkeypatch):
    _patch_cashflow(monkeypatch, 500)
    session.add(
        CreditCard(
            issuer="CardCo", card_name="Card", balance=5000, credit_limit=7000, apr=27, minimum_payment=200
        )
    )
    session.commit()
    sync_household_native_value_moves(session)
    row = session.exec(select(OpportunityCandidate).where(OpportunityCandidate.category == "debt_paydown")).one()
    math = json.loads(row.evidence_json)[0]["math"]
    assert row.value_horizon_days == 365
    assert math["value_30_days"] < math["value_365_days"] <= math["interest_avoided_lifetime"]
