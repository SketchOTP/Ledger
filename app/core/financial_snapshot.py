"""Immutable financial state snapshot — single source of truth per refresh cycle."""

import json
import re
from datetime import UTC, date, datetime
from statistics import median
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.core.account_balances import account_totals
from app.core.cashflow_engine import calculate_cashflow
from app.core.credit_card_optimizer import analyze_credit_card
from app.core.debt_optimizer import rank_debts
from app.core.dti_engine import calculate_dti
from app.core.health_score import calculate_health_score
from app.core.mortgage_optimizer import analyze_mortgage
from app.core.risk_engine import scan_risks
from app.db.models import Bill, BorrowerProfile, CreditCard, Debt, IncomeSource, MortgageAccount, Transaction


class FinancialStateSnapshot(BaseModel):
    """Frozen view of household finances for one refresh cycle."""

    snapshot_id: str = Field(default_factory=lambda: str(uuid4()))
    generated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"))

    cashflow: dict[str, Any] = Field(default_factory=dict)
    money_state: dict[str, Any] = Field(default_factory=dict)
    debts: dict[str, Any] = Field(default_factory=dict)
    credit: list[dict[str, Any]] = Field(default_factory=list)
    dti: dict[str, Any] = Field(default_factory=dict)
    mortgage: list[dict[str, Any]] = Field(default_factory=list)
    groceries: dict[str, Any] = Field(default_factory=dict)
    research: list[dict[str, Any]] = Field(default_factory=list)
    risks: list[dict[str, Any]] = Field(default_factory=list)
    health: dict[str, Any] = Field(default_factory=dict)
    opportunities: list[dict[str, Any]] = Field(default_factory=list)

    raw_income_sources: list[dict[str, Any]] = Field(default_factory=list)
    raw_bills: list[dict[str, Any]] = Field(default_factory=list)
    raw_debts: list[dict[str, Any]] = Field(default_factory=list)
    raw_mortgages: list[dict[str, Any]] = Field(default_factory=list)
    raw_credit_cards: list[dict[str, Any]] = Field(default_factory=list)
    borrower: dict[str, Any] | None = None
    research_provenance: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"frozen": True}


def build_financial_snapshot(
    session: Session,
    *,
    research_offers: list[dict[str, Any]] | None = None,
    opportunities: list[dict[str, Any]] | None = None,
) -> FinancialStateSnapshot:
    """Build one immutable snapshot; all engines should consume this in a refresh cycle."""
    cf = calculate_cashflow(session)
    dti = calculate_dti(session)
    risks = scan_risks(session, cf)

    mortgages_raw = list(session.exec(select(MortgageAccount)).all())
    market_rate = _market_rate_from_research(research_offers or [])
    mortgage_analyses = [
        analyze_mortgage(m, estimated_market_rate=market_rate) for m in mortgages_raw
    ]

    cards_raw = list(session.exec(select(CreditCard)).all())
    bt_rate = _bt_rate_from_research(research_offers or [])
    credit_analyses = [analyze_credit_card(c, market_bt_apr=bt_rate) for c in cards_raw]

    borrower = session.exec(select(BorrowerProfile)).first()
    borrower_dict = borrower.model_dump() if borrower else None
    if borrower_dict is not None:
        totals = account_totals(session)
        borrower_dict["checking_balance"] = totals["checking"]
        borrower_dict["emergency_fund_balance"] = totals["savings"]

    return FinancialStateSnapshot(
        cashflow=cf,
        money_state=dict(cf.get("money_state") or {}),
        debts={
            "avalanche": rank_debts(session, "avalanche"),
            "snowball": rank_debts(session, "snowball"),
            "hybrid": rank_debts(session, "hybrid"),
        },
        credit=credit_analyses,
        dti=dti,
        mortgage=mortgage_analyses,
        # Grocery product data is historical-only in V2; ordinary grocery
        # transactions remain part of generic variable spending.
        groceries={},
        # Historical FinancialOffer rows remain exportable, but V2 research
        # must be supplied by the canonical D003 pipeline rather than the
        # retired offer-comparison runtime.
        research=research_offers or [],
        risks=risks,
        health=calculate_health_score(session),
        opportunities=opportunities or [],
        raw_income_sources=[s.model_dump() for s in session.exec(select(IncomeSource)).all()],
        raw_bills=[b.model_dump() for b in session.exec(select(Bill)).all()],
        raw_debts=[d.model_dump() for d in session.exec(select(Debt)).all()],
        raw_mortgages=[m.model_dump() for m in mortgages_raw],
        raw_credit_cards=[c.model_dump() for c in cards_raw],
        borrower=borrower_dict,
        research_provenance=[],
    )


def snapshot_to_context(snapshot: FinancialStateSnapshot) -> dict[str, Any]:
    """Dict for AI explain-only layer (never for math)."""
    cf = snapshot.cashflow
    return {
        "snapshot_id": snapshot.snapshot_id,
        "generated_at": snapshot.generated_at,
        "cashflow": cf,
        "money_state": snapshot.money_state,
        "dti": snapshot.dti,
        "health": snapshot.health,
        "risks": snapshot.risks,
        "debt_strategies": snapshot.debts,
        "mortgages": snapshot.mortgage,
        "credit_cards": snapshot.credit,
        "offers": snapshot.research,
        "grocery": snapshot.groceries,
        "opportunities": snapshot.opportunities,
        "answers": {
            "can_pay_before_payday": cf.get("risk_level") != "danger",
            "debt_inefficient": any(r.get("code") == "high_apr_debt" for r in snapshot.risks),
            "mortgage_worth_monitoring": any(
                m.get("recommendation") in ("monitor", "compare_lenders", "refinance_candidate")
                for m in snapshot.mortgage
            ),
            "dti_hurting": snapshot.dti.get("status") in ("constrained", "high-risk"),
            "opportunity_count": len(snapshot.opportunities),
        },
    }


def _market_rate_from_research(offers: list[dict[str, Any]]) -> float | None:
    for o in offers:
        if o.get("product_type") in ("mortgage_refinance", "mortgage_rate") and o.get("estimated_apr") is not None:
            return float(o["estimated_apr"])
    return None


def _bt_rate_from_research(offers: list[dict[str, Any]]) -> float | None:
    for o in offers:
        if o.get("product_type") == "balance_transfer" and o.get("estimated_apr") is not None:
            return float(o["estimated_apr"])
    return None


_RECURRING_CADENCES: tuple[tuple[str, float, float], ...] = (
    ("weekly", 7.0, 2.0),
    ("biweekly", 14.0, 3.0),
    ("monthly", 30.4375, 5.0),
    ("quarterly", 91.3125, 10.0),
    ("semiannual", 182.625, 18.0),
    ("annual", 365.25, 28.0),
)
_ANNUAL_OCCURRENCES = {
    "weekly": 365.25 / 7,
    "biweekly": 365.25 / 14,
    "monthly": 12.0,
    "quarterly": 4.0,
    "semiannual": 2.0,
    "annual": 1.0,
}


def annual_occurrences(cadence: str | None) -> float | None:
    """Return the canonical annual occurrence count for a detected cadence."""
    return _ANNUAL_OCCURRENCES.get(str(cadence or "").strip().lower())


def current_recurring_evidence(candidate: Any) -> dict[str, Any] | None:
    """Select the newest recurrence observation from append-only candidate evidence.

    Recurrence evidence is stored as an append-only list.  The observation with
    the greatest last-transaction date is current; list position is only a
    deterministic tie-break when observations describe the same date.
    """
    try:
        items = json.loads(getattr(candidate, "evidence_json", "[]") or "[]")
    except (TypeError, json.JSONDecodeError):
        return None
    observations: list[tuple[date, int, dict[str, Any]]] = []
    for index, item in enumerate(items if isinstance(items, list) else []):
        if not isinstance(item, dict) or not isinstance(item.get("math"), dict):
            continue
        math = item["math"]
        merchant = str(math.get("merchant_key") or "").strip()
        cadence = str(math.get("cadence") or "").strip().lower()
        amount = math.get("avg_amount")
        last_raw = math.get("last_transaction_date")
        if not merchant or not cadence or amount is None or not last_raw:
            continue
        try:
            last_date = date.fromisoformat(str(last_raw))
            typical = float(amount)
        except (TypeError, ValueError):
            continue
        occurrences = annual_occurrences(cadence)
        if occurrences is None or typical < 0:
            continue
        annual_cost = math.get("estimated_annual_cost")
        try:
            annual = float(annual_cost) if annual_cost is not None else typical * occurrences
        except (TypeError, ValueError):
            annual = typical * occurrences
        observations.append(
            (
                last_date,
                index,
                {
                    "merchant_key": merchant,
                    "cadence": cadence,
                    "cadence_days": float(math.get("cadence_days") or 0),
                    "avg_amount": typical,
                    "estimated_annual_cost": round(annual, 2),
                    "amount_behavior": str(math.get("amount_behavior") or "UNKNOWN"),
                    "confidence": str(math.get("confidence") or "UNKNOWN"),
                    "first_transaction_date": math.get("first_transaction_date"),
                    "last_transaction_date": last_date.isoformat(),
                },
            )
        )
    if not observations:
        return None
    return max(observations, key=lambda row: (row[0], row[1]))[2]
_NON_OUTFLOW_WORDS = {
    "refund",
    "rebate",
    "cashback",
    "reimbursement",
    "deposit",
    "transfer",
    "payroll",
    "salary",
    "income",
    "paycheck",
    "payment",
    "credit",
}
_NON_OUTFLOW_CATEGORIES = {
    "income",
    "refund",
    "rebate",
    "cashback",
    "deposit",
    "transfer",
    "payroll",
    "salary",
    "payment",
    "debt_payment",
    "mortgage_payment",
    "credit_card_payment",
}


def normalize_transaction_merchant(value: str) -> str:
    """Normalize only obvious descriptor/processor noise, never fuzzy-match merchants."""
    text = (value or "").strip().lower()
    text = re.sub(r"^(?:pos|ach|debit|credit|purchase|card|check)\s*[-:#/]?\s*", "", text)
    text = re.sub(r"\b(?:sq|square|paypal|venmo|stripe)\s*[*#:/-]?\s*", "", text)
    text = re.sub(r"\b(?:ref|txn|trace|auth|transaction)\s*[-:#]?\s*[a-z0-9-]+$", "", text)
    text = re.sub(r"\b\d{3,}\b", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _is_household_outflow(tx: Transaction) -> bool:
    """Transaction imports use positive amounts for spending/outflows."""
    if float(tx.amount or 0) <= 0:
        return False
    category = normalize_transaction_merchant(tx.category)
    description = normalize_transaction_merchant(tx.description)
    if category in _NON_OUTFLOW_CATEGORIES:
        return False
    words = set(f"{category} {description}".split())
    return not words.intersection(_NON_OUTFLOW_WORDS)


def _required_merchant_keys(session: Session) -> set[str]:
    """Build conservative identities for obligations already known to the household."""
    from app.db.models import Bill, CreditCard, Debt, MortgageAccount

    values: list[str] = []
    values.extend(str(row.name or "") for row in session.exec(select(Bill)).all() if row.required)
    values.extend(str(row.creditor or "") for row in session.exec(select(Debt)).all())
    values.extend(str(row.issuer or "") for row in session.exec(select(CreditCard)).all())
    values.extend(str(row.card_name or "") for row in session.exec(select(CreditCard)).all())
    values.extend(str(row.lender or "") for row in session.exec(select(MortgageAccount)).all())
    return {key for value in values if (key := normalize_transaction_merchant(value))}


def _matches_required(merchant_key: str, required_keys: set[str]) -> bool:
    if not merchant_key:
        return True
    merchant_words = set(merchant_key.split())
    for required in required_keys:
        if merchant_key == required:
            return True
        required_words = set(required.split())
        shared = merchant_words.intersection(required_words)
        if (
            shared
            and any(len(word) >= 4 for word in shared)
            and len(shared) >= max(1, min(len(merchant_words), len(required_words)) // 2)
        ):
            return True
    return False


def _cadence(gaps: list[int]) -> tuple[str, float, float] | None:
    if not gaps:
        return None
    for name, target, tolerance in _RECURRING_CADENCES:
        if all(abs(gap - target) <= tolerance for gap in gaps):
            return name, target, tolerance
    return None


def detect_recurring_subscriptions(session: Session) -> list[dict[str, Any]]:
    """Detect credible recurring outflows using cadence and robust amounts.

    Imported transaction amounts are positive outflows by contract.  Credits,
    refunds, income, transfers, and payment-like rows are excluded because the
    model has no separate signed/type field.  The detector establishes only
    recurrence; cancellation and savings remain owner-confirmed facts.
    """
    groups: dict[str, list[Transaction]] = {}
    required_keys = _required_merchant_keys(session)
    for tx in session.exec(select(Transaction)).all():
        if not _is_household_outflow(tx):
            continue
        merchant_key = normalize_transaction_merchant(tx.description or tx.category)
        if not merchant_key or _matches_required(merchant_key, required_keys):
            continue
        groups.setdefault(merchant_key, []).append(tx)

    patterns: list[dict[str, Any]] = []
    for merchant_key, rows in groups.items():
        ordered = sorted(rows, key=lambda row: row.transaction_date)
        if len(ordered) < 2:
            continue
        gaps = [
            (right.transaction_date - left.transaction_date).days
            for left, right in zip(ordered, ordered[1:], strict=False)
        ]
        cadence = _cadence(gaps)
        if cadence is None:
            continue
        cadence_name, cadence_days, _tolerance = cadence
        amounts = [float(row.amount) for row in ordered]
        typical = float(median(amounts))
        max_deviation = max(abs(amount - typical) for amount in amounts)
        deviation_ratio = max_deviation / max(typical, 1.0)
        if deviation_ratio <= 0.05 or max_deviation <= 2.0:
            amount_behavior = "FIXED"
        elif deviation_ratio <= 0.15:
            amount_behavior = "VARIABLE_STABLE"
        else:
            amount_behavior = "VARIABLE_AMBIGUOUS"
        if amount_behavior == "VARIABLE_AMBIGUOUS":
            continue
        confidence = "HIGH" if len(ordered) >= 3 and amount_behavior == "FIXED" else "MEDIUM"
        annual_cost = typical * _ANNUAL_OCCURRENCES[cadence_name]
        patterns.append(
            {
                "description": (ordered[-1].description or ordered[-1].category).strip(),
                "merchant_key": merchant_key,
                "canonical_identity": f"recurring:{merchant_key}",
                "occurrences": len(ordered),
                "avg_amount": round(typical, 2),
                "estimated_annual_cost": round(annual_cost, 2),
                "cadence": cadence_name,
                "cadence_days": cadence_days,
                "amount_behavior": amount_behavior,
                "confidence": confidence,
                "classification": "POTENTIALLY_OPTIONAL",
                "first_transaction_date": ordered[0].transaction_date.isoformat(),
                "last_transaction_date": ordered[-1].transaction_date.isoformat(),
                "transaction_ids": [row.id for row in ordered],
            }
        )
    return sorted(patterns, key=lambda row: (-float(row["estimated_annual_cost"]), row["merchant_key"]))[:10]
