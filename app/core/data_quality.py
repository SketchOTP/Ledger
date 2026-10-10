"""Data quality and confidence layer — trust gate for CFO recommendations."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from app.core.income_aggregate import annual_income_from_sources
from app.db.models import (
    Bill,
    BorrowerProfile,
    CreditCard,
    Debt,
    FinancialOffer,
    IncomeSource,
    MortgageAccount,
)
from app.events.bus import event_bus
from app.events.types import Event, EventType
from app.research.ingestion.types import STALE_AFTER_DAYS
from app.research.providers.curated_rates import _load_curated

DOMAINS = (
    "income",
    "bills",
    "debts",
    "mortgage",
    "credit_cards",
    "research_rates",
    "borrower_profile",
)

RATE_SENSITIVE_ACTIONS = frozenset(
    {
        "refinance_compare",
        "balance_transfer_compare",
        "personal_loan_compare",
        "change_grocery_store",
        "switch_provider",
        "increase_emergency_fund",
    }
)

SAFETY_ACTIONS = frozenset({"hold_cash", "pay_bill", "avoid_action"})


def assess_data_quality(session: Session, *, emit_events: bool = True) -> dict[str, Any]:
    """Score each financial domain and produce an overall trust report."""
    domain_results = [
        _assess_income(session),
        _assess_bills(session),
        _assess_debts(session),
        _assess_mortgage(session),
        _assess_credit_cards(session),
        _assess_research_rates(session),
        _assess_borrower_profile(session),
    ]

    blocking_issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    recommended_fixes: list[str] = []

    for domain in domain_results:
        for issue in domain["issues"]:
            entry = {
                "domain": domain["domain"],
                "check": issue["check"],
                "message": issue["message"],
                "severity": issue["severity"],
                "entity_id": issue.get("entity_id"),
            }
            if issue["severity"] == "blocking":
                blocking_issues.append(entry)
            else:
                warnings.append(entry)

    for domain in domain_results:
        recommended_fixes.extend(domain.get("recommended_fixes", []))

    overall_score = round(
        sum(d["score"] for d in domain_results) / len(domain_results),
        1,
    )
    status = _overall_status(overall_score, blocking_issues, domain_results)

    report = {
        "overall_score": overall_score,
        "status": status,
        "domains": domain_results,
        "blocking_issues": blocking_issues,
        "warnings": warnings,
        "recommended_fixes": _dedupe(recommended_fixes),
        "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }

    if emit_events:
        event_bus.emit(
            Event(
                event_type=EventType.DATA_QUALITY_CHECKED.value,
                source="data_quality",
                payload={
                    "overall_score": overall_score,
                    "status": status,
                    "blocking_count": len(blocking_issues),
                    "warning_count": len(warnings),
                },
            )
        )
        for issue in blocking_issues:
            event_bus.emit(
                Event(
                    event_type=EventType.DATA_QUALITY_BLOCKING_ISSUE_DETECTED.value,
                    source="data_quality",
                    severity="warning",
                    payload=issue,
                )
            )

    return report


def apply_data_quality_gate(
    session: Session,
    recs: list[dict[str, Any]],
    report: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Block or downgrade recommendations when underlying data is untrustworthy."""
    report = report or assess_data_quality(session, emit_events=False)
    status = report["status"]
    blocking = report["blocking_issues"]
    blocked_codes = {i["check"] for i in blocking}

    gated: list[dict[str, Any]] = []
    stats = {"blocked": 0, "downgraded": 0, "allowed": 0}

    for rec in recs:
        rec = dict(rec)
        action = rec.get("action_type", "")
        should_block, block_reason = _should_block_rec(rec, status, blocking, blocked_codes)

        if should_block:
            rec["blocked_by"] = list(set(rec.get("blocked_by", []) + ["data_quality", block_reason]))
            rec["confidence"] = min(float(rec.get("confidence", 0.8)), 0.15)
            rec["requires_manual_verification"] = True
            rec["why_generated"] = list(rec.get("why_generated", [rec.get("why", "")])) + [
                f"Blocked: {block_reason}"
            ]
            stats["blocked"] += 1
            event_bus.emit(
                Event(
                    event_type=EventType.RECOMMENDATION_BLOCKED_DUE_TO_DATA_QUALITY.value,
                    source="data_quality",
                    severity="warning",
                    payload={
                        "title": rec.get("title"),
                        "action_type": action,
                        "reason": block_reason,
                        "data_quality_status": status,
                    },
                )
            )
        elif status in ("weak", "usable") and action in RATE_SENSITIVE_ACTIONS:
            old_conf = float(rec.get("confidence", 0.8))
            factor = 0.55 if status == "weak" else 0.75
            rec["confidence"] = round(old_conf * factor, 3)
            rec["requires_manual_verification"] = True
            rec["why_generated"] = list(rec.get("why_generated", [rec.get("why", "")])) + [
                f"Downgraded due to {status} data quality (score {report['overall_score']})"
            ]
            stats["downgraded"] += 1
            event_bus.emit(
                Event(
                    event_type=EventType.RECOMMENDATION_DOWNGRADED_DUE_TO_DATA_QUALITY.value,
                    source="data_quality",
                    payload={
                        "title": rec.get("title"),
                        "action_type": action,
                        "old_confidence": old_conf,
                        "new_confidence": rec["confidence"],
                        "data_quality_status": status,
                    },
                )
            )
        else:
            if status == "usable" and report["warnings"]:
                rec["why_generated"] = list(rec.get("why_generated", [rec.get("why", "")])) + [
                    "Data quality usable with warnings — verify before acting"
                ]
            stats["allowed"] += 1

        gated.append(rec)

    report = {**report, "gate_stats": stats}
    return gated, report


def _should_block_rec(
    rec: dict[str, Any],
    status: str,
    blocking: list[dict[str, Any]],
    blocked_codes: set[str],
) -> tuple[bool, str]:
    action = rec.get("action_type", "")

    if action in SAFETY_ACTIONS:
        return False, ""

    if status == "unsafe":
        return True, "overall data quality unsafe"

    if action == "refinance_compare" and (
        "missing_mortgage_market_rate" in blocked_codes
        or _domain_unsafe(blocking, "mortgage")
        or _domain_unsafe(blocking, "research_rates")
    ):
        return True, "missing or unreliable mortgage market rate"

    if action == "balance_transfer_compare" and (
        "missing_apr" in blocked_codes or _domain_unsafe(blocking, "credit_cards")
    ):
        return True, "credit card data incomplete (APR required)"

    if action in RATE_SENSITIVE_ACTIONS and _domain_unsafe(blocking, "research_rates"):
        return True, "research rate data unsafe"

    return False, ""


def _is_blocked(rec: dict[str, Any]) -> bool:
    return "data_quality" in rec.get("blocked_by", [])


def _domain_unsafe(blocking: list[dict[str, Any]], domain: str) -> bool:
    return any(i["domain"] == domain and i["severity"] == "blocking" for i in blocking)


def _overall_status(
    overall_score: float,
    blocking_issues: list[dict[str, Any]],
    domains: list[dict[str, Any]],
) -> str:
    if blocking_issues or overall_score < 40:
        return "unsafe"
    if any(d["status"] == "unsafe" for d in domains) or overall_score < 60:
        return "weak"
    if overall_score >= 85 and len(blocking_issues) == 0:
        return "trusted"
    return "usable"


def _domain_result(
    domain: str,
    issues: list[dict[str, Any]],
    *,
    empty_ok: bool = False,
) -> dict[str, Any]:
    score = 100
    fixes: list[str] = []

    for issue in issues:
        if issue["severity"] == "blocking":
            score -= 35
            fixes.append(issue.get("fix", issue["message"]))
        else:
            score -= 12
            fixes.append(issue.get("fix", issue["message"]))

    if not issues and not empty_ok:
        score = 70  # no data recorded yet

    score = max(0, min(100, score))
    has_blocking = any(i["severity"] == "blocking" for i in issues)
    if has_blocking or score < 40:
        status = "unsafe"
    elif score < 60:
        status = "weak"
    elif score >= 85:
        status = "trusted"
    else:
        status = "usable"

    return {
        "domain": domain,
        "score": score,
        "status": status,
        "issues": issues,
        "recommended_fixes": fixes,
        "checks_passed": max(0, 8 - len(issues)),
        "checks_failed": len(issues),
    }


def _issue(
    check: str,
    message: str,
    *,
    severity: str = "warning",
    entity_id: str | None = None,
    fix: str | None = None,
) -> dict[str, Any]:
    return {
        "check": check,
        "message": message,
        "severity": severity,
        "entity_id": entity_id,
        **({"fix": fix} if fix else {}),
    }


def _assess_income(session: Session) -> dict[str, Any]:
    sources = list(session.exec(select(IncomeSource)).all())
    issues: list[dict[str, Any]] = []

    if not sources:
        issues.append(
            _issue(
                "missing_required_fields",
                "No income sources recorded",
                severity="blocking",
                fix="Add at least one income source with amount and pay frequency",
            )
        )
        return _domain_result("income", issues, empty_ok=True)

    for src in sources:
        if not src.amount or src.amount <= 0:
            issues.append(
                _issue(
                    "impossible_values",
                    f"Income '{src.source_name}' has invalid amount",
                    severity="blocking",
                    entity_id=src.id,
                    fix=f"Set a positive amount for {src.source_name}",
                )
            )
        if not src.pay_frequency:
            issues.append(
                _issue(
                    "missing_required_fields",
                    f"Income '{src.source_name}' missing pay frequency",
                    severity="blocking",
                    entity_id=src.id,
                )
            )
        if src.next_pay_date is None:
            issues.append(
                _issue(
                    "missing_required_fields",
                    f"Income '{src.source_name}' missing next pay date",
                    entity_id=src.id,
                    fix=f"Set next_pay_date for {src.source_name}",
                )
            )
        elif src.next_pay_date < date.today() - timedelta(days=60):
            issues.append(
                _issue(
                    "stale_values",
                    f"Income '{src.source_name}' next pay date is stale",
                    entity_id=src.id,
                    fix="Update next_pay_date to the upcoming paycheck",
                )
            )

    return _domain_result("income", issues)


def _assess_bills(session: Session) -> dict[str, Any]:
    bills = list(session.exec(select(Bill)).all())
    issues: list[dict[str, Any]] = []

    if not bills:
        issues.append(
            _issue(
                "missing_required_fields",
                "No bills recorded",
                fix="Add recurring bills with amounts and due dates",
            )
        )
        return _domain_result("bills", issues, empty_ok=True)

    for bill in bills:
        if not bill.name or bill.amount is None:
            issues.append(
                _issue(
                    "missing_required_fields",
                    "Bill missing name or amount",
                    severity="blocking",
                    entity_id=bill.id,
                )
            )
        if bill.amount is not None and bill.amount < 0:
            issues.append(
                _issue(
                    "impossible_values",
                    f"Bill '{bill.name}' has negative amount",
                    severity="blocking",
                    entity_id=bill.id,
                )
            )
        if bill.required and bill.due_date is None:
            issues.append(
                _issue(
                    "missing_required_fields",
                    f"Required bill '{bill.name}' missing due date",
                    entity_id=bill.id,
                    fix=f"Set due_date for {bill.name}",
                )
            )

    return _domain_result("bills", issues)


def _assess_debts(session: Session) -> dict[str, Any]:
    debts = list(session.exec(select(Debt)).all())
    issues: list[dict[str, Any]] = []

    if not debts:
        return _domain_result("debts", issues, empty_ok=True)

    for debt in debts:
        if not debt.creditor or debt.balance is None or debt.minimum_payment is None:
            issues.append(
                _issue(
                    "missing_required_fields",
                    f"Debt '{debt.creditor or debt.id}' missing required fields",
                    severity="blocking",
                    entity_id=debt.id,
                )
            )
        if debt.apr is None or debt.apr <= 0:
            issues.append(
                _issue(
                    "missing_required_fields",
                    f"Debt '{debt.creditor}' missing APR",
                    severity="blocking",
                    entity_id=debt.id,
                    fix=f"Enter APR for {debt.creditor}",
                )
            )
        elif debt.apr > 80:
            issues.append(
                _issue(
                    "impossible_values",
                    f"Debt '{debt.creditor}' APR {debt.apr}% looks impossible",
                    entity_id=debt.id,
                )
            )
        if debt.balance is not None and debt.balance < 0:
            issues.append(
                _issue(
                    "impossible_values",
                    f"Debt '{debt.creditor}' has negative balance",
                    severity="blocking",
                    entity_id=debt.id,
                )
            )

    return _domain_result("debts", issues)


def _assess_mortgage(session: Session) -> dict[str, Any]:
    mortgages = list(session.exec(select(MortgageAccount)).all())
    issues: list[dict[str, Any]] = []

    if not mortgages:
        return _domain_result("mortgage", issues, empty_ok=True)

    for m in mortgages:
        if not m.lender or m.current_balance is None or m.interest_rate is None:
            issues.append(
                _issue(
                    "missing_required_fields",
                    f"Mortgage '{m.lender or m.id}' missing required fields",
                    severity="blocking",
                    entity_id=m.id,
                )
            )
        if m.interest_rate is not None and (m.interest_rate <= 0 or m.interest_rate > 20):
            issues.append(
                _issue(
                    "impossible_values",
                    f"Mortgage '{m.lender}' rate {m.interest_rate}% out of range",
                    entity_id=m.id,
                )
            )
        if m.monthly_payment is not None and m.monthly_payment <= 0:
            issues.append(
                _issue(
                    "impossible_values",
                    f"Mortgage '{m.lender}' has invalid monthly payment",
                    severity="blocking",
                    entity_id=m.id,
                )
            )
        if (
            m.property_value_estimate
            and m.current_balance
            and m.current_balance > m.property_value_estimate * 1.05
        ):
            issues.append(
                _issue(
                    "conflicting_values",
                    f"Mortgage '{m.lender}' balance exceeds property value estimate",
                    entity_id=m.id,
                )
            )

    has_market_rate = _has_mortgage_market_rate(session)
    if not has_market_rate:
        issues.append(
            _issue(
                "missing_mortgage_market_rate",
                "No verified mortgage market rate — refinance analysis blocked",
                severity="blocking",
                fix="Approve mortgage rate research or enter estimated_market_rate",
            )
        )

    return _domain_result("mortgage", issues)


def _assess_credit_cards(session: Session) -> dict[str, Any]:
    cards = list(session.exec(select(CreditCard)).all())
    issues: list[dict[str, Any]] = []

    if not cards:
        return _domain_result("credit_cards", issues, empty_ok=True)

    for card in cards:
        label = card.card_name or card.issuer or card.id
        if not card.issuer or card.balance is None or card.credit_limit is None:
            issues.append(
                _issue(
                    "missing_required_fields",
                    f"Card '{label}' missing required fields",
                    severity="blocking",
                    entity_id=card.id,
                )
            )
        if card.apr is None or card.apr <= 0:
            issues.append(
                _issue(
                    "missing_apr",
                    f"Card '{label}' missing APR",
                    severity="blocking",
                    entity_id=card.id,
                    fix=f"Enter APR for {label}",
                )
            )
        elif card.apr > 80:
            issues.append(
                _issue(
                    "impossible_values",
                    f"Card '{label}' APR {card.apr}% looks impossible",
                    entity_id=card.id,
                )
            )
        if card.credit_limit and card.balance and card.balance > card.credit_limit * 1.01:
            issues.append(
                _issue(
                    "conflicting_values",
                    f"Card '{label}' balance exceeds credit limit",
                    entity_id=card.id,
                )
            )
        if card.notes and "unverified" in card.notes.lower():
            issues.append(
                _issue(
                    "unverified_manual_entry",
                    f"Card '{label}' marked unverified in notes",
                    entity_id=card.id,
                )
            )

    return _domain_result("credit_cards", issues)


def _assess_research_rates(session: Session) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    offers = list(session.exec(select(FinancialOffer)).all())

    curated = _load_curated() or {}
    for section, key in (
        ("hysa", "hysa"),
        ("balance_transfer_benchmark", "balance_transfer_benchmark"),
        ("mortgage_rates", "mortgage_rates"),
        ("insurance_benchmark", "insurance_median_premium_monthly"),
        ("personal_loan_rates", "personal_loan_rates"),
    ):
        entries = curated.get(key)
        if isinstance(entries, dict):
            entries = [entries]
        if not entries:
            continue
        for entry in entries if isinstance(entries, list) else [entries]:
            if isinstance(entry, dict):
                _check_research_entry(entry, section, issues)

    low_confidence_seen: set[str] = set()
    for offer in offers:
        if not offer.source_url:
            issues.append(
                _issue(
                    "missing_source_url",
                    f"Offer '{offer.provider}' missing source URL",
                    entity_id=offer.id,
                    fix="Add source_url to financial offer",
                )
            )
        if offer.confidence < 0.4:
            dedupe_key = f"{offer.provider}:{offer.product_type}"
            if dedupe_key in low_confidence_seen:
                continue
            low_confidence_seen.add(dedupe_key)
            issues.append(
                _issue(
                    "low_source_confidence",
                    f"Offer '{offer.provider}' has low confidence ({offer.confidence})",
                    entity_id=offer.id,
                    fix="Re-run research after updating curated benchmarks or remove stale offers",
                )
            )
        age = datetime.now(UTC).replace(tzinfo=None) - offer.fetched_at
        if age > timedelta(days=STALE_AFTER_DAYS):
            issues.append(
                _issue(
                    "stale_values",
                    f"Offer '{offer.provider}' research is stale ({age.days} days old)",
                    entity_id=offer.id,
                    fix="Re-fetch or approve updated research",
                )
            )

    if not _has_mortgage_market_rate(session):
        issues.append(
            _issue(
                "missing_mortgage_market_rate",
                "No current mortgage market rate with last_checked date",
                severity="blocking",
                fix="Approve mortgage_rates ingestion with as_of date",
            )
        )

    if not offers and not curated:
        issues.append(
            _issue(
                "missing_required_fields",
                "No research rate data available",
                fix="Refresh canonical research data before comparing rate-sensitive actions",
            )
        )

    return _domain_result("research_rates", issues, empty_ok=True)


def _check_research_entry(entry: dict[str, Any], section: str, issues: list[dict[str, Any]]) -> None:
    label = entry.get("provider") or entry.get("source_label") or section
    if not entry.get("source_url"):
        issues.append(
            _issue(
                "missing_source_url",
                f"Curated {section} entry '{label}' missing source URL",
                fix=f"Add source_url to curated {section} data",
            )
        )
    last_checked = entry.get("as_of") or entry.get("last_checked")
    if not last_checked:
        issues.append(
            _issue(
                "missing_last_checked",
                f"Curated {section} entry '{label}' missing last_checked/as_of date",
                fix=f"Add as_of date to curated {section} entry",
            )
        )
    elif _parse_date(last_checked) and _parse_date(last_checked) < date.today() - timedelta(days=STALE_AFTER_DAYS):
        issues.append(
            _issue(
                "stale_values",
                f"Curated {section} rate for '{label}' is stale (as_of {last_checked})",
                fix=f"Refresh curated {section} rates",
            )
        )
    confidence = float(entry.get("confidence", 0.7))
    if confidence < 0.4:
        issues.append(
            _issue(
                "low_source_confidence",
                f"Curated {section} entry '{label}' has low confidence",
            )
        )


def _assess_borrower_profile(session: Session) -> dict[str, Any]:
    profile = session.exec(select(BorrowerProfile)).first()
    issues: list[dict[str, Any]] = []

    if profile is None:
        issues.append(
            _issue(
                "missing_required_fields",
                "Borrower profile not configured",
                fix="Create borrower profile with credit score and income",
            )
        )
        return _domain_result("borrower_profile", issues, empty_ok=True)

    if profile.credit_score is None:
        issues.append(
            _issue(
                "missing_required_fields",
                "Borrower profile missing credit score",
                entity_id=profile.id,
                fix="Enter credit_score on borrower profile",
            )
        )
    elif profile.credit_score < 300 or profile.credit_score > 850:
        issues.append(
            _issue(
                "impossible_values",
                f"Credit score {profile.credit_score} out of valid range",
                severity="blocking",
                entity_id=profile.id,
            )
        )

    sources = list(session.exec(select(IncomeSource)).all())
    derived_annual = annual_income_from_sources(sources)

    if profile.annual_income <= 0 and profile.household_income <= 0:
        if derived_annual > 0:
            issues.append(
                _issue(
                    "income_from_sources_only",
                    (
                        f"Income recorded in paychecks (~${derived_annual:,.0f}/yr) — "
                        "profile will sync on next income save"
                    ),
                    entity_id=profile.id,
                    fix="Save an income source or set household income on Cash profile",
                )
            )
        else:
            issues.append(
                _issue(
                    "missing_required_fields",
                    "Borrower profile missing income",
                    severity="blocking",
                    entity_id=profile.id,
                    fix="Add income under My Money → Income",
                )
            )

    if (
        profile.annual_income > 0
        and profile.household_income > 0
        and profile.household_income < profile.annual_income * 0.5
    ):
        issues.append(
            _issue(
                "conflicting_values",
                "Household income much lower than annual income — verify values",
                entity_id=profile.id,
            )
        )

    stale_cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=180)
    if profile.updated_at < stale_cutoff:
        issues.append(
            _issue(
                "stale_values",
                "Borrower profile has not been updated in over 6 months",
                entity_id=profile.id,
                fix="Review and update borrower profile",
            )
        )

    return _domain_result("borrower_profile", issues)


def _has_mortgage_market_rate(session: Session) -> bool:
    curated = _load_curated() or {}
    mr = curated.get("mortgage_rates")
    if isinstance(mr, dict) and mr.get("rate_30yr") and (mr.get("as_of") or mr.get("last_checked")):
        return True

    for offer in session.exec(select(FinancialOffer)).all():
        if "mortgage" in offer.product_type and offer.estimated_apr and offer.source_url:
            return True

    return False


def _parse_date(value: str | date | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
