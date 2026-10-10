"""D024 executable validation-task and prerequisite orchestration guards."""

from sqlmodel import Session

from app.core.v2_opportunities import (
    candidate_applicability,
    decision_pool,
    resolve_next_validation_task,
)
from app.db.models import CapabilitySkill, CreditCard, LiquidAccount, MortgageAccount, OpportunityCandidate


def _candidate(session: Session, category: str, identity: str) -> OpportunityCandidate:
    row = OpportunityCandidate(
        identity_key=identity,
        title=category,
        category=category,
        source_type="researched_external",
        verification_status="verified",
        freshness_status="current",
        lifecycle_status="researched",
        qualification_status="pending",
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def test_cash_yield_without_account_resolves_household_prerequisite(session):
    candidate = _candidate(session, "cash_yield_benchmark", "cash-prerequisite")

    task = resolve_next_validation_task(session)

    assert task["task_type"] == "HOUSEHOLD_PREREQUISITE"
    assert task["target_route"] == "/money?section=cash_on_hand"
    assert task["candidates_helped"] == 1
    assert "expected" not in task["instruction"].lower()
    assert candidate in decision_pool(session)
    assert candidate_applicability(session, candidate)["status"] == "PREREQUISITE_MISSING"


def test_cash_yield_changes_to_account_fact_after_household_account_exists(session):
    _candidate(session, "cash_yield_benchmark", "cash-account-fact")
    session.add(LiquidAccount(account_name="Savings", account_type="savings", balance=1000))
    session.commit()

    task = resolve_next_validation_task(session)

    assert task["task_type"] == "OWNER_FACT"
    assert task["fact_type"] == "comparison_account_id"
    assert "probability" not in task["instruction"].lower()
    assert "net" not in task["instruction"].lower()


def test_inapplicable_categories_do_not_become_owner_tasks(session):
    mortgage = _candidate(session, "mortgage_refinance", "no-mortgage")
    card = _candidate(session, "credit_card", "no-card")

    assert candidate_applicability(session, mortgage)["status"] == "NOT_CURRENTLY_APPLICABLE"
    assert candidate_applicability(session, card)["status"] == "NOT_CURRENTLY_APPLICABLE"
    assert mortgage not in decision_pool(session)
    assert card not in decision_pool(session)
    assert resolve_next_validation_task(session)["task_type"] == "NO_ACTIONABLE_VALIDATION"


def test_mortgage_and_card_tasks_require_household_context_first(session):
    mortgage = _candidate(session, "mortgage_refinance", "mortgage-task")
    card = _candidate(session, "credit_card", "card-task")
    session.add(
        MortgageAccount(
            lender="Example", original_balance=200000, current_balance=190000,
            interest_rate=7.0, monthly_payment=1500,
        )
    )
    session.add(
        CreditCard(
            issuer="Example", card_name="Card", balance=2000, credit_limit=5000, apr=24, minimum_payment=80
        )
    )
    session.commit()

    task = resolve_next_validation_task(session)

    assert task["task_type"] == "OWNER_FACT"
    assert task["candidate_id"] in {mortgage.id, card.id}
    assert task["fact_type"] not in {"expected_net_range", "probability_range", "capital_at_risk"}


def test_growth_task_never_requests_probability(session):
    session.add(CapabilitySkill(name="Python automation"))
    candidate = _candidate(session, "capability_matched_growth", "growth-fact")
    task = resolve_next_validation_task(session)

    assert task["candidate_id"] == candidate.id
    assert task["task_type"] in {"OWNER_FACT", "VALIDATION_EXPERIMENT", "EXTERNAL_FACT"}
    assert task["fact_type"] not in {"probability_low", "probability_high", "success_probability"}
    assert "probability" not in task["instruction"].lower()


def test_only_derived_missing_values_yield_no_owner_task(session):
    row = OpportunityCandidate(
        identity_key="derived-only",
        title="Derived only",
        category="other",
        source_type="owner_supplied",
        verification_status="verified",
        freshness_status="current",
        lifecycle_status="researched",
        qualification_status="pending",
    )
    session.add(row)
    session.commit()

    task = resolve_next_validation_task(session)

    assert task["task_type"] == "NO_ACTIONABLE_VALIDATION"
    assert "net" not in task["instruction"].lower()
    assert "probability" not in task["instruction"].lower()
