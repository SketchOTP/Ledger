"""Onboarding wizard — completeness scoring and CFO readiness gates."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, select

from app.ai.provider_registry import ProviderConfigurationError, build_ai_settings_payload
from app.core.data_quality import _has_mortgage_market_rate, assess_data_quality
from app.db.models import (
    Bill,
    BorrowerProfile,
    CreditCard,
    Debt,
    IncomeSource,
    LiquidAccount,
    MortgageAccount,
    OnboardingState,
)
from app.research.providers.curated_rates import _load_curated

SECTION_ORDER = (
    "income",
    "cash_on_hand",
    "bills",
    "debts",
    "mortgage",
    "credit_cards",
    "credit_score",
    "target_buffer",
    "ai_provider_check",
)

# Sections that do not count toward setup wizard completion (post-setup / optional).
OPTIONAL_COMPLETION_SECTIONS = frozenset({"credit_score", "target_buffer", "ai_provider_check"})

# Minimum score to leave the setup wizard (income + cash + bills required).
GOOD_ENOUGH_COMPLETION_SCORE = 45.0

SECTION_LABELS = {
    "income": "Income & paycheck",
    "cash_on_hand": "Cash on hand",
    "bills": "Bills",
    "debts": "Debts",
    "mortgage": "Housing",
    "credit_cards": "Credit cards",
    "credit_score": "Credit score",
    "target_buffer": "Emergency buffer goal",
    "ai_provider_check": "AI assistant",
}

SECTION_WEIGHTS: dict[str, float] = {
    "income": 12.0,
    "cash_on_hand": 10.0,
    "bills": 12.0,
    "debts": 8.0,
    "mortgage": 10.0,
    "credit_cards": 8.0,
    "credit_score": 8.0,
    "target_buffer": 5.0,
    "ai_provider_check": 12.0,
}

NEXT_STEP_HINTS = {
    "income": "Add your paycheck under My Money → Paycheck.",
    "cash_on_hand": "Enter checking and savings balances under My Money → Cash.",
    "bills": "Add repeating bills with amounts and due dates under My Money → Bills.",
    "debts": "List debts with APR and minimum payments, or mark that you have none.",
    "mortgage": "Add your mortgage details, or mark that you do not have a mortgage.",
    "credit_cards": (
        "Add each card with balance, limit, APR, minimum payment, and due date — "
        "or leave blank if you have none."
    ),
    "credit_score": "Enter your credit score on your household profile.",
    "target_buffer": "Optional — set a savings buffer goal under My Money → Cash if you want one.",
    "ai_provider_check": "Optional — connect AI for richer summaries, or skip and use deterministic advice.",
}


def get_onboarding_state(session: Session) -> OnboardingState:
    state = session.get(OnboardingState, "default")
    if not state:
        state = OnboardingState(
            id="default",
            declarations_json=json.dumps(
                {
                    "no_mortgage": False,
                    "no_credit_cards": False,
                    "no_debts": False,
                    "no_bills": False,
                    "skip_grocery": False,
                    "research_acknowledged": False,
                }
            ),
        )
        session.add(state)
        session.commit()
        session.refresh(state)
    return state


def is_onboarding_complete(session: Session) -> bool:
    """Whether the user may use the app without the setup wizard.

    Once ``completed_at`` is set, the wizard is not shown again on login even if
    new checklist items are added later (e.g. target buffer). Use household
    setup / My Money for ongoing gaps instead of re-blocking the whole app.

    Good-enough mode: income, cash on hand, and at least one bill unlock the
    dashboard with limited-confidence deterministic advice (no AI required).
    """
    state = session.get(OnboardingState, "default")
    if not state:
        return False
    if state.completed_at:
        return True
    assessment = assess_onboarding(session, emit_events=False)
    core_complete = _core_sections_complete(assessment["sections"])
    return core_complete and assessment["completion_score"] >= GOOD_ENOUGH_COMPLETION_SCORE


def _core_sections_complete(sections: list[dict[str, Any]]) -> bool:
    return all(s["complete"] or s.get("skipped") for s in sections[:3])


def get_declarations(session: Session) -> dict[str, bool]:
    state = get_onboarding_state(session)
    try:
        raw = json.loads(state.declarations_json or "{}")
    except json.JSONDecodeError:
        raw = {}
    return {
        "no_mortgage": bool(raw.get("no_mortgage")),
        "no_credit_cards": bool(raw.get("no_credit_cards")),
        "no_debts": bool(raw.get("no_debts")),
        "no_bills": bool(raw.get("no_bills")),
        "skip_grocery": bool(raw.get("skip_grocery")),
        "research_acknowledged": bool(raw.get("research_acknowledged")),
    }


def update_declarations(session: Session, updates: dict[str, bool]) -> OnboardingState:
    state = get_onboarding_state(session)
    decl = get_declarations(session)
    for key in (
        "no_mortgage",
        "no_credit_cards",
        "no_debts",
        "no_bills",
        "skip_grocery",
        "research_acknowledged",
    ):
        if key in updates:
            decl[key] = bool(updates[key])
    state.declarations_json = json.dumps(decl)
    state.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(state)
    session.commit()
    session.refresh(state)
    return state


def mark_step_completed(session: Session, step_id: str) -> OnboardingState:
    state = get_onboarding_state(session)
    steps = _steps_list(state)
    if step_id not in steps:
        steps.append(step_id)
    state.steps_completed_json = json.dumps(steps)
    state.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(state)
    session.commit()
    session.refresh(state)
    return state


def _steps_list(state: OnboardingState) -> list[str]:
    try:
        return list(json.loads(state.steps_completed_json or "[]"))
    except json.JSONDecodeError:
        return []


def _section(
    *,
    section_id: str,
    complete: bool,
    missing_fields: list[str],
    blocking_issues: list[str] | None = None,
    skipped: bool = False,
    score: float | None = None,
) -> dict[str, Any]:
    if skipped:
        return {
            "id": section_id,
            "label": SECTION_LABELS.get(section_id, section_id),
            "complete": True,
            "skipped": True,
            "score": 100.0,
            "missing_fields": [],
            "blocking_issues": [],
        }
    missing = list(missing_fields)
    blocking = list(blocking_issues or [])
    if score is None:
        if not complete and any(
            m.startswith("at_least") or m.endswith("_or_declare_none") for m in missing
        ):
            score = 0.0
        else:
            penalty = min(100, len(missing) * 25 + len(blocking) * 15)
            score = float(max(0, 100 - penalty))
    return {
        "id": section_id,
        "label": SECTION_LABELS.get(section_id, section_id),
        "complete": complete,
        "skipped": False,
        "score": round(score, 1),
        "missing_fields": missing,
        "blocking_issues": blocking,
    }


def _assess_income(session: Session) -> dict[str, Any]:
    sources = list(session.exec(select(IncomeSource)).all())
    missing: list[str] = []
    if not sources:
        missing.append("income_source")
        return _section(section_id="income", complete=False, missing_fields=missing)

    for src in sources:
        if not src.source_name or not src.amount:
            missing.append(f"{src.id}:name_or_amount")
        if not src.next_pay_date:
            missing.append(f"{src.id}:next_pay_date")
    complete = len(missing) == 0
    return _section(section_id="income", complete=complete, missing_fields=missing)


def _assess_cash_on_hand(session: Session) -> dict[str, Any]:
    profile = session.exec(select(BorrowerProfile)).first()
    missing: list[str] = []
    if not profile:
        missing.append("borrower_profile")
        return _section(section_id="cash_on_hand", complete=False, missing_fields=missing)

    extra = list(session.exec(select(LiquidAccount)).all())
    extra_checking = sum(float(a.balance or 0) for a in extra if (a.account_type or "checking") == "checking")
    extra_savings = sum(float(a.balance or 0) for a in extra if (a.account_type or "") == "savings")
    has_cash = (
        float(profile.checking_balance or 0) + extra_checking > 0
        or float(profile.emergency_fund_balance or 0) + extra_savings > 0
    )
    if not has_cash:
        missing.append("checking_or_savings_balance")
    return _section(section_id="cash_on_hand", complete=has_cash, missing_fields=missing)


def _assess_bills(session: Session, decl: dict[str, bool]) -> dict[str, Any]:
    del decl  # bills are required for setup; no "no bills" skip
    bills = list(session.exec(select(Bill)).all())
    missing: list[str] = []
    if not bills:
        missing.append("at_least_one_bill")
        return _section(section_id="bills", complete=False, missing_fields=missing)

    for bill in bills:
        if not bill.name or bill.amount is None:
            missing.append(f"{bill.id}:name_or_amount")
        if bill.required and not bill.due_date:
            missing.append(f"{bill.id}:due_date")
    complete = len(missing) == 0
    return _section(section_id="bills", complete=complete, missing_fields=missing)


def _assess_debts(session: Session, decl: dict[str, bool]) -> dict[str, Any]:
    del decl  # optional: no debts = leave form blank and continue
    debts = list(session.exec(select(Debt)).all())
    if not debts:
        return _section(section_id="debts", complete=True, missing_fields=[], skipped=True)

    missing: list[str] = []
    for debt in debts:
        if not debt.creditor or debt.balance is None or debt.minimum_payment is None:
            missing.append(f"{debt.id}:required_fields")
        if debt.apr is None or debt.apr <= 0:
            missing.append(f"{debt.id}:apr")
    complete = len(missing) == 0
    return _section(section_id="debts", complete=complete, missing_fields=missing)


def _assess_mortgage(session: Session, decl: dict[str, bool]) -> dict[str, Any]:
    del decl
    profile = session.exec(select(BorrowerProfile)).first()
    status = (profile.housing_status or "rent").lower() if profile else "rent"

    if status == "rent":
        missing: list[str] = []
        if not profile or float(profile.monthly_rent or 0) <= 0:
            missing.append("monthly_rent")
        if not profile or not profile.rent_due_date:
            missing.append("rent_due_date")
        return _section(
            section_id="mortgage",
            complete=len(missing) == 0,
            missing_fields=missing,
        )

    mortgages = list(session.exec(select(MortgageAccount)).all())
    missing = []
    blocking: list[str] = []
    if not mortgages:
        missing.append("mortgage_account")
        return _section(section_id="mortgage", complete=False, missing_fields=missing)

    for m in mortgages:
        if m.interest_rate is None or m.interest_rate <= 0:
            missing.append(f"{m.id}:interest_rate")
        if m.current_balance is None:
            missing.append(f"{m.id}:current_balance")
        if m.monthly_payment is None or m.monthly_payment <= 0:
            missing.append(f"{m.id}:monthly_payment")
        if m.property_value_estimate is None or m.property_value_estimate <= 0:
            missing.append(f"{m.id}:property_value_estimate")

    if mortgages and not _has_mortgage_market_rate(session):
        blocking.append("mortgage_market_rate_not_approved")

    complete = len(missing) == 0
    return _section(
        section_id="mortgage",
        complete=complete,
        missing_fields=missing,
        blocking_issues=blocking,
    )


def _assess_credit_cards(session: Session, decl: dict[str, bool]) -> dict[str, Any]:
    del decl  # optional: no cards = leave form blank and continue
    cards = list(session.exec(select(CreditCard)).all())
    if not cards:
        return _section(section_id="credit_cards", complete=True, missing_fields=[], skipped=True)

    missing: list[str] = []
    for card in cards:
        label = card.card_name or card.issuer or card.id
        if card.balance is None:
            missing.append(f"{label}:balance")
        if card.credit_limit is None or card.credit_limit <= 0:
            missing.append(f"{label}:credit_limit")
        if card.apr is None or card.apr <= 0:
            missing.append(f"{label}:apr")
        if card.minimum_payment is None:
            missing.append(f"{label}:minimum_payment")

    complete = len(missing) == 0
    return _section(section_id="credit_cards", complete=complete, missing_fields=missing)


def _assess_credit_score(session: Session) -> dict[str, Any]:
    profile = session.exec(select(BorrowerProfile)).first()
    missing: list[str] = []
    if not profile or profile.credit_score is None:
        missing.append("credit_score")
        return _section(section_id="credit_score", complete=False, missing_fields=missing)
    if profile.credit_score < 300 or profile.credit_score > 850:
        missing.append("credit_score_out_of_range")
    complete = len(missing) == 0
    return _section(section_id="credit_score", complete=complete, missing_fields=missing)


def _assess_target_buffer(session: Session) -> dict[str, Any]:
    """Optional — empty/zero means no emergency buffer goal (not a blocker)."""
    profile = session.exec(select(BorrowerProfile)).first()
    if not profile or float(profile.target_buffer or 0) <= 0:
        return _section(section_id="target_buffer", complete=True, missing_fields=[], skipped=True)
    return _section(section_id="target_buffer", complete=True, missing_fields=[])


def _has_approved_curated_research() -> bool:
    curated = _load_curated() or {}
    if not curated:
        return False
    for key in ("hysa", "mortgage_rates", "balance_transfer_benchmark"):
        block = curated.get(key)
        if isinstance(block, dict) and block.get("source_url"):
            return True
        if isinstance(block, list) and block and isinstance(block[0], dict) and block[0].get("source_url"):
            return True
    return False


def _assess_ai_provider(skip_health_check: bool = False) -> dict[str, Any]:
    missing: list[str] = []
    blocking: list[str] = []
    health: dict[str, Any] = {}
    if not skip_health_check:
        try:
            from app.ai.providers import get_provider

            health = get_provider().health_check()
        except ProviderConfigurationError as exc:
            health = {"ok": False, "error": str(exc)}
        except Exception as exc:
            health = {"ok": False, "error": str(exc)}

    payload = build_ai_settings_payload(health_check=health if health else None)
    if payload["status"] == "misconfigured":
        missing.append("ai_provider_configuration")
        blocking.extend(payload.get("warnings") or [])
    elif payload["status"] == "unavailable" and not skip_health_check:
        missing.append("ai_provider_unreachable")
    if skip_health_check:
        complete = payload["status"] == "configured"
    else:
        complete = payload["status"] == "configured" and bool(health.get("ok"))
    return _section(
        section_id="ai_provider_check",
        complete=complete,
        missing_fields=missing,
        blocking_issues=blocking,
        score=100.0 if complete else 30.0,
    )


def credit_optimization_ready(session: Session, decl: dict[str, bool]) -> dict[str, Any]:
    del decl
    cards = list(session.exec(select(CreditCard)).all())
    if not cards:
        return {"ready": True, "skipped": True, "blocking": []}
    blocking: list[str] = []
    for card in cards:
        label = card.card_name or card.issuer or card.id
        if card.apr is None or card.apr <= 0:
            blocking.append(f"{label}: missing APR")
        if card.balance is None:
            blocking.append(f"{label}: missing balance")
        if card.credit_limit is None or card.credit_limit <= 0:
            blocking.append(f"{label}: missing credit limit")
        if card.minimum_payment is None:
            blocking.append(f"{label}: missing minimum payment")
    return {"ready": len(blocking) == 0, "skipped": False, "blocking": blocking}


def mortgage_optimization_ready(session: Session, decl: dict[str, bool]) -> dict[str, Any]:
    del decl
    profile = session.exec(select(BorrowerProfile)).first()
    if profile and (profile.housing_status or "rent").lower() == "rent":
        return {"ready": True, "skipped": True, "blocking": []}
    mortgages = list(session.exec(select(MortgageAccount)).all())
    if not mortgages:
        return {"ready": True, "skipped": True, "blocking": []}
    blocking: list[str] = []
    for m in mortgages:
        label = m.lender or m.id
        if m.interest_rate is None or m.interest_rate <= 0:
            blocking.append(f"{label}: missing rate")
        if m.current_balance is None:
            blocking.append(f"{label}: missing balance")
        if m.monthly_payment is None or m.monthly_payment <= 0:
            blocking.append(f"{label}: missing payment")
        if m.property_value_estimate is None or m.property_value_estimate <= 0:
            blocking.append(f"{label}: missing property value")
    if not _has_mortgage_market_rate(session):
        blocking.append("approved mortgage market rate required")
    return {"ready": len(blocking) == 0, "skipped": False, "blocking": blocking}


def is_ai_report_ready(session: Session | None = None) -> bool:
    """AI-enhanced CFO report only when provider is configured and healthy."""
    section = _assess_ai_provider()
    return bool(section["complete"])


def assess_onboarding(
    session: Session,
    *,
    emit_events: bool = False,
    skip_ai_health_check: bool = False,
) -> dict[str, Any]:
    state = get_onboarding_state(session)
    decl = get_declarations(session)
    steps = _steps_list(state)

    sections = [
        _assess_income(session),
        _assess_cash_on_hand(session),
        _assess_bills(session, decl),
        _assess_debts(session, decl),
        _assess_mortgage(session, decl),
        _assess_credit_cards(session, decl),
        _assess_credit_score(session),
        _assess_target_buffer(session),
        _assess_ai_provider(skip_health_check=skip_ai_health_check),
    ]

    optional = OPTIONAL_COMPLETION_SECTIONS
    active_weight = 0.0
    weighted = 0.0
    for sec in sections:
        if sec["id"] in optional:
            continue
        w = SECTION_WEIGHTS[sec["id"]]
        active_weight += w
        weighted += w * float(sec["score"])

    completion_score = round(weighted / active_weight, 1) if active_weight else 0.0

    missing_required_fields: list[str] = []
    blocking_issues: list[str] = []
    for sec in sections:
        if sec.get("skipped"):
            continue
        if not sec["complete"]:
            for m in sec["missing_fields"]:
                missing_required_fields.append(f"{sec['id']}.{m}")
        blocking_issues.extend(sec.get("blocking_issues") or [])

    credit_opt = credit_optimization_ready(session, decl)
    mortgage_opt = mortgage_optimization_ready(session, decl)
    if not credit_opt["ready"] and not credit_opt.get("skipped"):
        blocking_issues.extend([f"credit_optimization:{b}" for b in credit_opt["blocking"]])
    if not mortgage_opt["ready"] and not mortgage_opt.get("skipped"):
        blocking_issues.extend([f"mortgage_optimization:{b}" for b in mortgage_opt["blocking"]])

    dq = assess_data_quality(session, emit_events=False)

    core_complete = _core_sections_complete(sections)

    if not state.started_at and completion_score < 5:
        status = "not_started"
    elif completion_score >= 85 and dq["overall_score"] >= 70 and not blocking_issues and core_complete:
        status = "trusted"
    elif completion_score >= 60 and core_complete:
        status = "usable"
    else:
        status = "incomplete"

    next_best_step = _pick_next_step(sections)

    report_readiness = {
        "deterministic": core_complete and completion_score >= GOOD_ENOUGH_COMPLETION_SCORE,
        "ai_enhanced": is_ai_report_ready(),
        "research_rates": _has_approved_curated_research() or _has_mortgage_market_rate(session),
        "credit_optimization": credit_opt["ready"],
        "mortgage_optimization": mortgage_opt["ready"],
    }

    confidence_mode = "full" if completion_score >= 100 else "limited" if report_readiness["deterministic"] else "setup"

    setup_roadmap = _build_setup_roadmap(state, sections, completion_score, core_complete)

    payload = {
        "completion_score": completion_score,
        "status": status,
        "confidence_mode": confidence_mode,
        "good_enough_ready": report_readiness["deterministic"],
        "setup_roadmap": setup_roadmap,
        "missing_required_fields": missing_required_fields,
        "blocking_issues": blocking_issues,
        "next_best_step": next_best_step,
        "data_quality_after_onboarding": dq,
        "sections": sections,
        "declarations": decl,
        "steps_completed": steps,
        "started_at": state.started_at.isoformat() if state.started_at else None,
        "completed_at": state.completed_at.isoformat() if state.completed_at else None,
        "optimization_readiness": {
            "credit": credit_opt,
            "mortgage": mortgage_opt,
        },
        "report_readiness": report_readiness,
    }

    if emit_events:
        from app.events.bus import event_bus
        from app.events.types import Event, EventType

        event_bus.emit(
            Event(
                event_type=EventType.ONBOARDING_STEP_COMPLETED.value,
                source="onboarding",
                payload={"completion_score": completion_score, "status": status},
            )
        )

    return payload


def _build_setup_roadmap(
    state: OnboardingState,
    sections: list[dict[str, Any]],
    completion_score: float,
    core_complete: bool,
) -> list[dict[str, Any]]:
    """Progressive household setup guide (Day 1 / 2 / 3 / 7)."""
    by_id = {s["id"]: s for s in sections}

    def _phase(day: int, title: str, section_ids: list[str], hint: str) -> dict[str, Any]:
        done = all(
            by_id.get(sid, {}).get("complete") or by_id.get(sid, {}).get("skipped")
            for sid in section_ids
        )
        days_since = 0
        if state.started_at:
            days_since = (datetime.now(UTC).replace(tzinfo=None).date() - state.started_at.date()).days
        return {
            "day": day,
            "title": title,
            "hint": hint,
            "complete": done,
            "available": days_since >= day - 1 or done or core_complete,
            "section_ids": section_ids,
        }

    return [
        _phase(
            1,
            "Cash, income & top bills",
            ["cash_on_hand", "income", "bills"],
            "Unlocks safe-to-spend and daily briefing.",
        ),
        _phase(2, "Debts & credit cards", ["debts", "credit_cards"], "Improves payoff and card advice."),
        _phase(3, "Rent / mortgage / housing", ["mortgage"], "Aligns housing costs with cashflow."),
        _phase(
            7,
            "Advice quality review",
            ["credit_score", "target_buffer"],
            "Optional polish — review Advice and Fix my data (Advanced mode).",
        ),
    ]


def _pick_next_step(sections: list[dict[str, Any]]) -> str:
    for sec in sections:
        if sec.get("skipped"):
            continue
        if not sec["complete"]:
            hint = NEXT_STEP_HINTS.get(sec["id"], sec["label"])
            return f"Complete {sec['label']}: {hint}"
    return "You're in good shape — run a CFO report to refresh advice."


def start_onboarding(session: Session) -> dict[str, Any]:
    state = get_onboarding_state(session)
    if not state.started_at:
        state.started_at = datetime.now(UTC).replace(tzinfo=None)
        state.updated_at = datetime.now(UTC).replace(tzinfo=None)
        session.add(state)
        session.commit()
    return assess_onboarding(session)


def complete_onboarding(session: Session) -> dict[str, Any]:
    assessment = assess_onboarding(session, emit_events=False)
    core_complete = _core_sections_complete(assessment["sections"])
    good_enough = core_complete and assessment["completion_score"] >= GOOD_ENOUGH_COMPLETION_SCORE
    if not good_enough:
        return {
            **assessment,
            "wizard_complete": False,
            "error": (
                "Add checking balance, income, and at least one bill first — "
                "then you can start using Ledger (AI optional)."
            ),
        }
    state = get_onboarding_state(session)
    state.completed_at = datetime.now(UTC).replace(tzinfo=None)
    state.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(state)
    session.commit()
    result = assess_onboarding(session)
    result["wizard_complete"] = True
    result["finished_with_limited_confidence"] = result["completion_score"] < 100
    return result
