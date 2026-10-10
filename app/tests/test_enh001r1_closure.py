"""ENH-001R1 closure proofs for risk, reserve, and hypothesis truth."""

from datetime import date, timedelta

from app.core.cashflow_engine import calculate_cashflow
from app.core.household_advisor import build_advisory, build_resilience, build_wealth_plan
from app.db.models import (
    Bill,
    BorrowerProfile,
    CapabilityAsset,
    CapabilityProfile,
    CapabilitySkill,
    HouseholdCoverage,
    HouseholdIncomeRisk,
    HouseholdProfile,
    IncomeSource,
)


def _base(session):
    session.add(BorrowerProfile(checking_balance=8_000, emergency_fund_balance=250))
    session.add(
        IncomeSource(
            source_name="Work", amount=4_000, pay_frequency="monthly", next_pay_date=date.today() + timedelta(days=4)
        )
    )
    session.add(Bill(name="Rent", amount=1_200, due_date=date.today() + timedelta(days=4)))
    session.commit()


def test_dedicated_reserve_is_not_d001_headroom(session):
    _base(session)
    result = build_resilience(session)
    assert result["current_reserve"] == 250
    assert result["current_reserve"] != result["current_accessible_liquidity"]


def test_income_risk_and_coverage_change_reserve_target(session):
    _base(session)
    baseline = build_resilience(session)
    session.add(
        HouseholdIncomeRisk(income_share=1, affected_percent=1, recovery_duration_months=9, stability_band="unstable")
    )
    session.commit()
    exposed = build_resilience(session)
    assert exposed["target"]["recommended"] > baseline["target"]["recommended"]
    session.add(HouseholdCoverage(coverage_type="income", status="adequate", out_of_pocket_max=100))
    session.commit()
    covered = build_resilience(session)
    assert covered["target"]["recommended"] <= exposed["target"]["recommended"]


def test_growth_is_zero_without_qualified_candidate_and_test_capital_is_truthful(session):
    _base(session)
    session.add(CapabilityProfile(max_preferred_capital=100))
    session.add(CapabilitySkill(name="Python", monetization_willingness=True))
    session.add(CapabilitySkill(name="Design", monetization_willingness=True))
    session.add(CapabilityAsset(name="Laptop", monetization_willingness=True))
    session.commit()
    advisory = build_advisory(session, generate_hypotheses=True)
    assert advisory["wealth_plan"]["allocation"]["grow"] == 0
    assert advisory["hypotheses"]
    assert advisory["hypotheses"][0]["required_capital"] == advisory["hypotheses"][0]["test_capital"]


def test_allocation_reconciles_and_respects_candidate_gate(session):
    _base(session)
    profile = HouseholdProfile(dedicated_resilience_balance=1_000)
    capability = CapabilityProfile(max_preferred_capital=100)
    session.add(profile)
    session.add(capability)
    session.commit()
    cf = calculate_cashflow(session)
    resilience = build_resilience(session, cf)
    plan = build_wealth_plan(cf, resilience, profile, capability, [{"required_capital": 25}])
    assert plan["allocation"]["grow"] <= 25
    assert plan["allocation"]["reconciles"] is True
