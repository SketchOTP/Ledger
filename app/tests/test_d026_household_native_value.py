"""D026 household-native value moves."""

import json

from sqlmodel import select

from app.core.financial_snapshot import FinancialStateSnapshot
from app.core.opportunity_engine import _subscription_opportunities, detect_opportunities
from app.core.v2_opportunities import (
    candidate_quality,
    normalize_v1_opportunities,
    qualify_candidate,
    ranked_candidates,
)
from app.core.v2_validation import save_fact
from app.db.models import CreditCard, Debt, OpportunityCandidate, Transaction


def _snapshot(*cards, deployable=1000):
    raw = [
        {
            "id": card.id,
            "issuer": card.issuer,
            "card_name": card.card_name,
            "balance": card.balance,
            "apr": card.apr,
            "minimum_payment": card.minimum_payment,
        }
        for card in cards
    ]
    return FinancialStateSnapshot(
        raw_credit_cards=raw,
        credit=[{"balance_transfer_candidate": False} for _ in cards],
        money_state={"deployable_surplus": deployable, "experimental_capital": deployable, "risk_windows": []},
        cashflow={"money_state": {"deployable_surplus": deployable}},
    )


def _debt_snapshot(*debts, deployable=1000):
    return FinancialStateSnapshot(
        raw_debts=[
            {
                "id": debt.id,
                "creditor": debt.creditor,
                "balance": debt.balance,
                "apr": debt.apr,
                "minimum_payment": debt.minimum_payment,
            }
            for debt in debts
        ],
        money_state={"deployable_surplus": deployable, "experimental_capital": deployable, "risk_windows": []},
        cashflow={"money_state": {"deployable_surplus": deployable}},
    )


def test_high_apr_native_move_is_deterministic_and_d001_capped(session, monkeypatch):
    card = CreditCard(
        issuer="Fixture",
        card_name="Card",
        balance=3000,
        credit_limit=5000,
        apr=24,
        minimum_payment=150,
    )
    session.add(card)
    session.commit()
    findings = [
        row for row in detect_opportunities(_snapshot(card, deployable=600), session)
        if row["category"] == "debt_paydown"
    ]
    assert len(findings) == 1
    assert findings[0]["math"]["max_safe_extra_payment"] == 600
    assert findings[0]["math"]["interest_avoided"] > 0
    monkeypatch.setattr(
        "app.core.v2_opportunities.calculate_cashflow",
        lambda _session: {
            "money_state": {"deployable_surplus": 600, "experimental_capital": 600, "risk_windows": []}
        },
    )
    candidate = normalize_v1_opportunities(session, findings)[0]
    result = qualify_candidate(session, candidate)
    assert result["eligible"] is True
    assert candidate.probability_low is None and candidate_quality(candidate) == "DECISION_GRADE"
    assert ranked_candidates(session)[0]["category"] == "debt_paydown"


def test_native_target_selects_strongest_and_skips_when_no_surplus(session):
    low = CreditCard(issuer="Low", card_name="Low", balance=3000, credit_limit=5000, apr=21, minimum_payment=150)
    high = CreditCard(issuer="High", card_name="High", balance=3000, credit_limit=5000, apr=29, minimum_payment=150)
    session.add_all([low, high])
    session.commit()
    findings = [
        row for row in detect_opportunities(_snapshot(low, high, deployable=1000), session)
        if row["category"] == "debt_paydown"
    ]
    assert len(findings) == 1 and findings[0]["math"]["debt_id"] == high.id
    assert not [
        row for row in detect_opportunities(_snapshot(low, high, deployable=0), session)
        if row["category"] == "debt_paydown"
    ]


def test_native_move_supports_standalone_debt(session):
    debt = Debt(creditor="Loan", balance=2400, apr=25, minimum_payment=120, debt_type="loan")
    session.add(debt)
    session.commit()
    findings = [
        row for row in detect_opportunities(_debt_snapshot(debt, deployable=400), session)
        if row["category"] == "debt_paydown"
    ]
    assert len(findings) == 1
    assert findings[0]["math"]["debt_id"] == debt.id
    assert findings[0]["math"]["max_safe_extra_payment"] == 400


def test_native_identity_refreshes_without_flooding(session):
    card = CreditCard(issuer="Fixture", card_name="Card", balance=3000, credit_limit=5000, apr=24, minimum_payment=150)
    session.add(card)
    session.commit()
    first = [
        row for row in detect_opportunities(_snapshot(card, deployable=600), session)
        if row["category"] == "debt_paydown"
    ]
    normalize_v1_opportunities(session, first)
    card.balance = 2500
    session.add(card)
    session.commit()
    second = [
        row for row in detect_opportunities(_snapshot(card, deployable=600), session)
        if row["category"] == "debt_paydown"
    ]
    normalize_v1_opportunities(session, second)
    rows = session.exec(select(OpportunityCandidate).where(OpportunityCandidate.category == "debt_paydown")).all()
    assert len(rows) == 1
    # A one-year interest differential can remain equal when the same extra
    # payment is applied and both schedules stay within the same horizon. The
    # refreshed candidate must nevertheless carry the current debt facts.
    evidence = json.loads(rows[0].evidence_json)
    assert any(item.get("math", {}).get("balance") == 2500 for item in evidence)


def test_native_move_clears_when_deployable_surplus_disappears(session):
    card = CreditCard(issuer="Fixture", card_name="Card", balance=3000, credit_limit=5000, apr=24, minimum_payment=150)
    session.add(card)
    session.commit()
    first = [
        row for row in detect_opportunities(_snapshot(card, deployable=600), session)
        if row["category"] == "debt_paydown"
    ]
    candidate = normalize_v1_opportunities(session, first)[0]
    assert candidate.expected_net_low is not None
    normalize_v1_opportunities(session, [])
    session.refresh(candidate)
    assert candidate.expected_net_low is None and candidate.safety_eligible is False


def test_recurring_uses_canonical_category_and_owner_confirmation(session):
    from datetime import date

    session.add_all([
        Transaction(description="streaming", amount=20, category="entertainment", transaction_date=date(2026, 1, 1)),
        Transaction(description="streaming", amount=20, category="entertainment", transaction_date=date(2026, 2, 1)),
    ])
    session.commit()
    finding = _subscription_opportunities(session)[0]
    assert finding["category"] == "subscription" and finding["estimated_yearly_savings"] == 0
    candidate = normalize_v1_opportunities(session, [finding])[0]
    save_fact(session, candidate, fact_type="can_cancel", value=True, units="boolean")
    save_fact(session, candidate, fact_type="new_recurring_amount", value=0, units="currency")
    assert candidate.expected_net_low == 240
    save_fact(session, candidate, fact_type="cancellation_fee", value=300, units="currency")
    assert candidate.expected_net_low == -60


def test_no_arbitrary_emergency_candidate_and_no_native_duplicate(session):
    snapshot = FinancialStateSnapshot(
        cashflow={"risk_level": "safe", "safe_to_spend": 5000},
        borrower={"emergency_fund_balance": 0},
    )
    assert not [row for row in detect_opportunities(snapshot, session) if row["leak_type"] == "underutilized_cash_plan"]
    assert "1000" not in json.dumps(detect_opportunities(snapshot, session))
