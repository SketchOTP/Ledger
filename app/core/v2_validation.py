"""Narrow owner-fact and deterministic candidate-economics boundary (D023)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from app.core.cashflow_engine import calculate_cashflow
from app.core.financial_snapshot import annual_occurrences, current_recurring_evidence
from app.core.opportunity_engine import _debt_amortization
from app.core.v2_opportunities import (
    DETERMINISTIC_ECONOMIC_CATEGORIES,
    candidate_payload,
    qualify_candidate,
    ranked_candidates,
)
from app.db.models import (
    CreditCard,
    Debt,
    LiquidAccount,
    MortgageAccount,
    OpportunityCandidate,
    OpportunityEvidence,
    OpportunityValidationFact,
)

SOURCE_TYPES = {"owner_entered", "owner_confirmed_quote", "owner_confirmed_statement", "external_evidence"}
VERIFICATION = {"owner_entered", "owner_confirmed", "external_verified"}

FACTS_BY_CATEGORY: dict[str, tuple[tuple[str, str, str], ...]] = {
    "cash_yield_benchmark": (
        ("comparison_account_id", "account", "The liquid account being compared"),
        ("comparison_fee", "currency", "Known transfer or account fee"),
        ("liquidity_lockup_days", "days", "Factual liquidity restriction"),
    ),
    "savings": (
        ("comparison_account_id", "account", "The liquid account being compared"),
        ("comparison_fee", "currency", "Known transfer or account fee"),
        ("liquidity_lockup_days", "days", "Factual liquidity restriction"),
    ),
    "mortgage_refinance": (
        ("quoted_apr", "percent", "APR on a current lender quote"),
        ("quoted_term_months", "months", "Quoted loan term"),
        ("closing_costs", "currency", "Known closing costs and points"),
        ("quoted_payment", "currency", "Quoted principal and interest payment, if supplied"),
        ("quote_expires_at", "date", "Quote expiration date"),
    ),
    "credit_card": (
        ("promotional_apr", "percent", "Verified promotional APR"),
        ("promotion_duration_months", "months", "Promotional period"),
        ("transfer_fee_percent", "percent", "Transfer fee as a percentage of the transferred balance"),
        ("transfer_fee_fixed", "currency", "Transfer fee as a fixed amount"),
        ("transfer_fee_minimum", "currency", "Minimum transfer fee when a percentage applies"),
        ("transfer_fee", "currency", "Legacy fixed transfer fee"),
        ("post_promo_apr", "percent", "Post-promotion APR, if known"),
        ("max_transferable_amount", "currency", "Stated transferable amount"),
    ),
    "recurring": (
        ("can_cancel", "boolean", "Whether the charge can be cancelled or downgraded"),
        ("new_recurring_amount", "currency", "Verified replacement recurring amount"),
        ("replacement_cadence", "cadence", "How often the replacement charge occurs, if different"),
        ("cancellation_fee", "currency", "Known cancellation fee"),
        ("actual_cancellation_fee", "currency", "Cancellation fee actually paid, if observed"),
    ),
    "subscription": (
        ("can_cancel", "boolean", "Whether the charge can be cancelled or downgraded"),
        ("new_recurring_amount", "currency", "Verified replacement recurring amount"),
        ("replacement_cadence", "cadence", "How often the replacement charge occurs, if different"),
        ("cancellation_fee", "currency", "Known cancellation fee"),
        ("actual_cancellation_fee", "currency", "Cancellation fee actually paid, if observed"),
    ),
}

REQUIRED_FACT_TYPES: dict[str, frozenset[str]] = {
    "cash_yield_benchmark": frozenset({"comparison_account_id"}),
    "savings": frozenset({"comparison_account_id"}),
    "mortgage_refinance": frozenset({"quoted_apr", "quoted_term_months", "closing_costs"}),
    "credit_card": frozenset({"promotional_apr", "promotion_duration_months"}),
    "recurring": frozenset({"can_cancel"}),
    "subscription": frozenset({"can_cancel"}),
}
OPTIONAL_FACT_TYPES = {
    "quoted_payment",
    "quote_expires_at",
    "post_promo_apr",
    "max_transferable_amount",
    "transfer_fee_percent",
    "transfer_fee_fixed",
    "transfer_fee_minimum",
    "transfer_fee",
    "comparison_fee",
    "liquidity_lockup_days",
    "cancellation_fee",
    "replacement_cadence",
    "actual_cancellation_fee",
}
CANONICAL_CADENCES = frozenset({"weekly", "biweekly", "monthly", "quarterly", "semiannual", "annual"})
GROWTH_FACTS = (
    ("stated_compensation", "currency", "Compensation explicitly stated by the source"),
    ("platform_fee", "currency", "Factual platform fee"),
    ("required_expense", "currency", "Known required expense"),
    ("owner_setup_hours", "hours", "Owner estimate of setup effort"),
    ("owner_recurring_hours", "hours_per_week", "Owner estimate of recurring effort"),
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _json_value(value: Any) -> str:
    try:
        return json.dumps(value, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("factual value must be JSON-compatible") from exc


def _value(row: OpportunityValidationFact) -> Any:
    try:
        return json.loads(row.factual_value_json)
    except json.JSONDecodeError:
        return None


def _spec(candidate: OpportunityCandidate) -> tuple[tuple[str, str, str], ...]:
    if candidate.category in FACTS_BY_CATEGORY:
        return FACTS_BY_CATEGORY[candidate.category]
    if candidate.category in {"capability_matched_growth", "productization", "income", "digital_product"}:
        return GROWTH_FACTS
    return ()


def _required_for_session(session: Session, candidate: OpportunityCandidate) -> set[str]:
    required = set(REQUIRED_FACT_TYPES.get(candidate.category, ()))
    if candidate.category == "credit_card":
        current = {
            row.fact_type
            for row in _current_facts(session, candidate.id)
            if row.status == "current" and not _is_stale(row)
        }
        if not current & {"transfer_fee_percent", "transfer_fee_fixed", "transfer_fee"}:
            required.add("transfer_fee")
    elif candidate.category in {"capability_matched_growth", "productization", "income", "digital_product"}:
        required = {"stated_compensation", "owner_setup_hours"}
    elif not required:
        required = {key for key, _units, _description in _spec(candidate) if key not in OPTIONAL_FACT_TYPES}
    return required


def _current_facts(session: Session, candidate_id: str) -> list[OpportunityValidationFact]:
    return list(
        session.exec(
            select(OpportunityValidationFact).where(
                OpportunityValidationFact.candidate_id == candidate_id,
                OpportunityValidationFact.status == "current",
            )
        ).all()
    )


def _is_stale(row: OpportunityValidationFact, now: datetime | None = None) -> bool:
    return row.expires_at is not None and row.expires_at <= (now or _now())


def _conflicting(facts: list[OpportunityValidationFact], fact_type: str) -> bool:
    values = [_json_value(_value(row)) for row in facts if row.fact_type == fact_type and not _is_stale(row)]
    return len(set(values)) > 1


EVIDENCE_RATE_CLAIMS = {
    "quoted_apr": {"rate", "apr", "annual_percentage_rate"},
    "promotional_apr": {"rate", "apr", "annual_percentage_rate"},
}


def _evidence_conflicts(session: Session, candidate: OpportunityCandidate, fact_type: str, value: Any) -> bool:
    claim_types = EVIDENCE_RATE_CLAIMS.get(fact_type)
    if not claim_types:
        return False
    try:
        owner_value = float(value)
    except (TypeError, ValueError):
        return False
    rows = session.exec(
        select(OpportunityEvidence).where(
            OpportunityEvidence.candidate_id == candidate.id,
            OpportunityEvidence.status == "current",
        )
    ).all()
    for row in rows:
        if row.claim_type not in claim_types or (row.expires_at is not None and row.expires_at <= _now()):
            continue
        try:
            evidence_value = float(json.loads(row.factual_value_json or "null"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if abs(evidence_value - owner_value) > 0.05:
            return True
    return False


def required_facts(candidate: OpportunityCandidate) -> list[dict[str, Any]]:
    return [
        {
            "fact_type": key,
            "units": units,
            "description": description,
            "required": key in REQUIRED_FACT_TYPES.get(candidate.category, set())
            or (
                candidate.category in {"capability_matched_growth", "productization", "income", "digital_product"}
                and key in {"stated_compensation", "owner_setup_hours"}
            ),
        }
        for key, units, description in _spec(candidate)
    ]


def validation_payload(session: Session, candidate: OpportunityCandidate) -> dict[str, Any]:
    facts = _current_facts(session, candidate.id)
    by_type = {row.fact_type: row for row in facts}
    missing: list[str] = []
    states: dict[str, str] = {}
    required_types = _required_for_session(session, candidate)
    for key, _units, _description in _spec(candidate):
        rows = [row for row in facts if row.fact_type == key]
        if not rows and key in required_types:
            missing.append(key)
            states[key] = "MISSING"
        elif rows and any(_is_stale(row) for row in rows):
            missing.append(key)
            states[key] = "STALE"
        elif rows and (_conflicting(facts, key) or _evidence_conflicts(session, candidate, key, _value(rows[-1]))):
            missing.append(key)
            states[key] = "CONFLICTING"
        elif not rows:
            states[key] = "OPTIONAL"
        else:
            row = by_type[key]
            states[key] = row.verification_status.upper()
    if candidate.category == "credit_card" and "transfer_fee" in required_types:
        for fee_type in ("transfer_fee_percent", "transfer_fee_fixed", "transfer_fee"):
            missing.append(fee_type)
            states[fee_type] = "MISSING"
    missing = list(dict.fromkeys(missing))
    known = []
    for row in facts:
        known.append(
            {
                "id": row.id,
                "fact_type": row.fact_type,
                "value": _value(row),
                "units": row.units,
                "source_type": row.source_type,
                "verification_status": "STALE" if _is_stale(row) else row.verification_status.upper(),
                "observed_at": row.observed_at,
                "expires_at": row.expires_at,
            }
        )
    allowed_inputs: dict[str, Any] = {}
    if any(key == "comparison_account_id" for key, _units, _description in _spec(candidate)):
        allowed_inputs["comparison_account_id"] = [
            {"id": account.id, "label": account.account_name}
            for account in session.exec(select(LiquidAccount).order_by(LiquidAccount.created_at.asc())).all()
        ]
    return {
        "candidate_id": candidate.id,
        "category": candidate.category,
        "required_facts": required_facts(candidate),
        "missing_fact_types": missing,
        "fact_states": states,
        "facts": known,
        "allowed_inputs": allowed_inputs,
        "next_step": candidate_payload(candidate)["decision_brief"]["next_step"],
    }


def save_fact(
    session: Session,
    candidate: OpportunityCandidate,
    *,
    fact_type: str,
    value: Any,
    units: str = "",
    source_type: str = "owner_entered",
    source_label: str = "",
    observed_at: datetime | None = None,
    expires_at: datetime | None = None,
    verification_status: str = "owner_entered",
) -> dict[str, Any]:
    spec = {key: spec_units for key, spec_units, _ in _spec(candidate)}
    if fact_type not in spec:
        raise ValueError("fact type is not permitted for this candidate")
    if source_type not in SOURCE_TYPES:
        raise ValueError("invalid fact source type")
    if source_type == "external_evidence":
        raise ValueError("external evidence is written by the D003 provenance path")
    if verification_status not in VERIFICATION:
        raise ValueError("invalid verification status")
    if verification_status == "external_verified":
        raise ValueError("external verification is written by the D003 provenance path")
    if units and units != spec[fact_type]:
        raise ValueError("fact units do not match the candidate contract")
    if (
        fact_type.endswith("apy")
        or fact_type.endswith("apr")
        or spec[fact_type] == "percent"
        or fact_type
        in {
            "post_promo_apr",
            "quoted_apr",
            "promotional_apr",
        }
    ):
        number = float(value)
        if number < 0 or number > 100:
            raise ValueError("rate must be between 0 and 100")
        value = number
    elif spec[fact_type] == "cadence":
        cadence = str(value or "").strip().lower()
        if cadence not in CANONICAL_CADENCES:
            raise ValueError("cadence must be weekly, biweekly, monthly, quarterly, semiannual, or annual")
        value = cadence
    elif spec[fact_type] in {"currency", "hours", "hours_per_week", "months", "days"}:
        number = float(value)
        if number < 0:
            raise ValueError("factual amount must be non-negative")
        value = number if spec[fact_type] not in {"months", "days"} else int(number)
    elif spec[fact_type] == "boolean" and not isinstance(value, bool):
        raise ValueError("boolean fact required")
    encoded = _json_value(value)
    current = session.exec(
        select(OpportunityValidationFact).where(
            OpportunityValidationFact.candidate_id == candidate.id,
            OpportunityValidationFact.fact_type == fact_type,
            OpportunityValidationFact.status == "current",
            OpportunityValidationFact.source_type == source_type,
        )
    ).first()
    if current and current.factual_value_json == encoded and current.expires_at == expires_at:
        return validation_payload(session, candidate)
    if current:
        current.status = "superseded"
        current.updated_at = _now()
        session.add(current)
    row = OpportunityValidationFact(
        candidate_id=candidate.id,
        fact_type=fact_type,
        factual_value_json=encoded,
        units=units or spec[fact_type],
        source_type=source_type,
        source_label=source_label[:500],
        observed_at=observed_at or _now(),
        expires_at=expires_at,
        verification_status="owner_confirmed" if verification_status == "owner_confirmed" else verification_status,
        supersedes_id=current.id if current else None,
    )
    session.add(row)
    session.commit()
    enrich_candidate_economics(session, candidate)
    qualify_candidate(session, candidate)
    ranked_candidates(session)
    session.refresh(candidate)
    return validation_payload(session, candidate)


def _facts_map(session: Session, candidate_id: str) -> dict[str, Any]:
    return {row.fact_type: _value(row) for row in _current_facts(session, candidate_id) if not _is_stale(row)}


def _annual_payment(principal: float, annual_rate: float, months: int) -> float:
    if months <= 0:
        return 0.0
    monthly = annual_rate / 100 / 12
    if monthly == 0:
        return principal / months
    return principal * monthly / (1 - (1 + monthly) ** -months)


def _amortization(principal: float, annual_rate: float, months: int, horizon: int | None = None) -> dict[str, float]:
    """Return payment, interest, and remaining balance over a fixed horizon."""
    months = max(1, int(months))
    horizon_months = min(months, max(1, int(horizon or months)))
    payment = _annual_payment(principal, annual_rate, months)
    balance = principal
    interest = 0.0
    for _ in range(horizon_months):
        interest_payment = balance * annual_rate / 100 / 12
        principal_payment = min(balance, max(0.0, payment - interest_payment))
        interest += interest_payment
        balance -= principal_payment
    total_interest = payment * months - principal
    return {
        "payment": payment,
        "interest": interest,
        "remaining_balance": max(0.0, balance),
        "total_interest": max(0.0, total_interest),
        "horizon_months": float(horizon_months),
    }


def _current_treasury_yield(session: Session, candidate: OpportunityCandidate) -> tuple[float, str] | None:
    rows = session.exec(
        select(OpportunityEvidence).where(
            OpportunityEvidence.candidate_id == candidate.id,
            OpportunityEvidence.provider_id == "treasury_yield_benchmark",
            OpportunityEvidence.claim_type == "yield",
            OpportunityEvidence.status == "current",
        )
    ).all()
    rows = [row for row in rows if row.expires_at is None or row.expires_at > _now()]
    for row in sorted(rows, key=lambda item: (item.retrieved_at, item.id), reverse=True):
        try:
            value = float(json.loads(row.factual_value_json or "null"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        return value, row.units or "percent_annualized"
    return None


def _clear_derived_economics(candidate: OpportunityCandidate) -> None:
    """Prevent expired/conflicting facts from retaining a prior decision grade."""
    if candidate.category not in DETERMINISTIC_ECONOMIC_CATEGORIES:
        return
    candidate.expected_gross_low = None
    candidate.expected_gross_high = None
    candidate.expected_net_low = None
    candidate.expected_net_high = None
    candidate.estimated_direct_cost = None
    candidate.required_capital = None
    candidate.capital_at_risk = None
    candidate.rank_score = None
    candidate.safety_eligible = False


def enrich_candidate_economics(
    session: Session,
    candidate: OpportunityCandidate,
    cashflow: dict[str, Any] | None = None,
) -> OpportunityCandidate:
    """Derive only economics supported by current financial truth and facts."""
    _clear_derived_economics(candidate)
    facts = _facts_map(session, candidate.id)
    assumptions: list[str] = []
    if candidate.category in {"cash_yield_benchmark", "savings"}:
        account_id = facts.get("comparison_account_id")
        account = session.get(LiquidAccount, str(account_id)) if account_id else None
        if account is None:
            account = session.exec(select(LiquidAccount).where(LiquidAccount.current_apy.is_not(None))).first()
        apy_current = bool(
            account
            and account.current_apy is not None
            and (account.apy_as_of is None or account.apy_as_of >= date.today() - timedelta(days=90))
        )
        benchmark = (
            _current_treasury_yield(session, candidate)
            if candidate.category == "cash_yield_benchmark"
            else None
        )
        if account and account.current_apy is not None and apy_current and benchmark is not None:
            money = (cashflow or calculate_cashflow(session)).get("money_state") or {}
            principal = min(
                max(0.0, float(money.get("deployable_surplus") or 0)),
                max(0.0, float(account.balance or 0)),
            )
            benchmark_rate, benchmark_units = benchmark
            if benchmark_units not in {"percent_annualized", "percent"}:
                assumptions.append(
                    "Treasury benchmark units are not comparable to bank APY without a defined conversion."
                )
                benchmark = None
            if benchmark is not None:
                gross = principal * (benchmark_rate - float(account.current_apy)) / 100
                fee = float(facts.get("comparison_fee") or 0)
                net = round(gross - fee, 2)
                candidate.expected_gross_low = candidate.expected_gross_high = round(gross, 2)
                candidate.estimated_direct_cost = fee
                candidate.expected_net_low = candidate.expected_net_high = net
                candidate.required_capital = round(principal, 2)
                candidate.capital_at_risk = 0.0
                candidate.liquidity_lockup_days = int(facts.get("liquidity_lockup_days") or 0)
                candidate.value_horizon_days = 365
                candidate.time_to_value_days = 365
                assumptions.append(
                    "Eligible principal is capped by D001 deployable surplus and the selected account balance."
                )
                assumptions.append(
                    f"Treasury benchmark is compared as a {benchmark_units} benchmark spread; "
                    "it is not converted to bank APY, and taxes/compounding are not modeled."
                )
        else:
            assumptions.append("Current account APY and a current Treasury benchmark are required before comparison.")
    elif candidate.category == "mortgage_refinance":
        quote = {key: facts.get(key) for key in ("quoted_apr", "quoted_term_months", "closing_costs")}
        mortgage = session.exec(select(MortgageAccount)).first()
        if mortgage and all(value is not None for value in quote.values()):
            balance = float(mortgage.current_balance or 0)
            old_months = int(mortgage.remaining_term_months or mortgage.term_years * 12)
            new_months = int(quote["quoted_term_months"] or 0)
            horizon = min(60, old_months, new_months)
            old = _amortization(balance, float(mortgage.interest_rate), old_months, horizon)
            new = _amortization(balance, float(quote["quoted_apr"]), new_months, horizon)
            horizon_interest_savings = old["interest"] - new["interest"]
            term_extension_penalty = max(0.0, new["total_interest"] - old["total_interest"])
            net = round(horizon_interest_savings - float(quote["closing_costs"] or 0) - term_extension_penalty, 2)
            candidate.expected_gross_low = candidate.expected_gross_high = round(horizon_interest_savings, 2)
            candidate.estimated_direct_cost = float(quote["closing_costs"] or 0)
            candidate.expected_net_low = candidate.expected_net_high = net
            candidate.required_capital = float(quote["closing_costs"] or 0)
            candidate.capital_at_risk = float(quote["closing_costs"] or 0)
            candidate.value_horizon_days = int(horizon * 30.4375)
            candidate.time_to_value_days = 30
            assumptions.append(
                "Interest comparison uses amortization over a 60-month-or-shorter horizon; "
                "full-term interest from a longer quote is conservatively penalized. "
                "Taxes and approval probability are not modeled."
            )
    elif candidate.category == "credit_card":
        card = session.exec(select(CreditCard)).first()
        required = ("promotional_apr", "promotion_duration_months")
        fee_kind = next(
            (
                key
                for key in ("transfer_fee_percent", "transfer_fee_fixed", "transfer_fee")
                if facts.get(key) is not None
            ),
            None,
        )
        if card and all(facts.get(key) is not None for key in required) and fee_kind:
            balance = min(float(card.balance or 0), float(facts.get("max_transferable_amount") or card.balance or 0))
            months = int(facts["promotion_duration_months"])
            current_cost = balance * float(card.apr) / 100 * months / 12
            if fee_kind == "transfer_fee_percent":
                transfer_fee = balance * float(facts[fee_kind]) / 100
                if facts.get("transfer_fee_minimum") is not None:
                    transfer_fee = max(transfer_fee, float(facts["transfer_fee_minimum"]))
            else:
                transfer_fee = float(facts[fee_kind])
            promo_cost = balance * float(facts["promotional_apr"]) / 100 * months / 12 + transfer_fee
            net = round(current_cost - promo_cost, 2)
            candidate.expected_gross_low = candidate.expected_gross_high = round(
                current_cost - balance * float(facts["promotional_apr"]) / 100 * months / 12,
                2,
            )
            candidate.estimated_direct_cost = transfer_fee
            candidate.expected_net_low = candidate.expected_net_high = net
            candidate.required_capital = 0.0
            candidate.capital_at_risk = 0.0
            candidate.value_horizon_days = months * 30
            candidate.time_to_value_days = 30
            assumptions.append(
                "Comparison uses current balance and stated terms; approval and "
                "post-promotion behavior are not modeled."
            )
    elif candidate.category == "debt_paydown":
        # Native debt moves derive from the canonical debt/card row and D001
        # capacity.  The detector stores the stable target id in its evidence
        # math so household edits can recompute the same candidate in place.
        target_id = None
        try:
            evidence_items = json.loads(candidate.evidence_json or "[]")
        except json.JSONDecodeError:
            evidence_items = []
        for item in evidence_items:
            if isinstance(item, dict) and isinstance(item.get("math"), dict):
                target_id = item["math"].get("debt_id")
                if target_id:
                    break
        target = session.get(CreditCard, str(target_id)) if target_id else None
        if target is None and target_id:
            target = session.get(Debt, str(target_id))
        if target is not None:
            balance = max(0.0, float(target.balance or 0))
            apr = max(0.0, float(target.apr or 0))
            minimum = max(0.0, float(target.minimum_payment or 0))
            money = (cashflow or calculate_cashflow(session)).get("money_state") or {}
            extra = round(min(balance, max(0.0, float(money.get("deployable_surplus") or 0))), 2)
            if extra > 0 and minimum > 0 and apr > 0:
                baseline = _debt_amortization(balance, apr, minimum)
                accelerated = _debt_amortization(balance, apr, minimum, extra=extra)
                if baseline["lifetime_interest"] < float("inf") and accelerated["lifetime_interest"] < float("inf"):
                    horizons = {
                        days: round(
                            max(
                                0.0,
                                baseline["interest_by_days"][days] - accelerated["interest_by_days"][days],
                            ),
                            2,
                        )
                        for days in (30, 60, 90, 180, 365)
                    }
                    avoided = horizons[365]
                    candidate.expected_gross_low = candidate.expected_gross_high = avoided
                    candidate.expected_net_low = candidate.expected_net_high = avoided
                    candidate.required_capital = extra
                    candidate.capital_at_risk = 0.0
                    candidate.estimated_direct_cost = 0.0
                    candidate.value_horizon_days = 365
                    candidate.time_to_value_days = 30
                    assumptions.append(
                        "Interest avoided uses a declining-balance simulation from the stated balance, APR, "
                        "minimum payment, and D001 safe capacity; the ranked value is the calculated 365-day "
                        "difference and lifetime value is retained only as supporting detail."
                    )
    elif candidate.category in {"recurring", "subscription"}:
        # Observed recurring spend is only a ceiling until the owner confirms
        # cancellation/downgrade and any replacement cost.  Never assume a
        # percentage of the charge is avoidable.
        recurrence = current_recurring_evidence(candidate)
        if facts.get("can_cancel") is True and recurrence is not None:
            cadence = str(recurrence["cadence"])
            replacement_cadence = str(facts.get("replacement_cadence") or cadence).strip().lower()
            replacement = float(facts.get("new_recurring_amount") or 0)
            replacement_occurrences = annual_occurrences(replacement_cadence)
            if replacement_occurrences is None:
                return candidate
            gross = float(recurrence["estimated_annual_cost"])
            if facts.get("new_recurring_amount") is not None:
                gross -= replacement * replacement_occurrences
            gross = round(max(0.0, gross), 2)
            fee = max(0.0, float(facts.get("cancellation_fee") or 0))
            candidate.expected_gross_low = candidate.expected_gross_high = gross
            candidate.estimated_direct_cost = fee
            candidate.expected_net_low = candidate.expected_net_high = round(gross - fee, 2)
            candidate.required_capital = fee
            candidate.capital_at_risk = 0.0
            candidate.value_horizon_days = 365
            candidate.time_to_value_days = max(1, round(float(recurrence.get("cadence_days") or 30.0)))
            assumptions.append(
                "Recurring savings use the latest cadence observation, owner-confirmed cancellation, "
                "replacement amount/cadence when supplied, and stated cancellation fee."
            )
    elif candidate.category in {"capability_matched_growth", "productization", "income", "digital_product"}:
        if facts.get("stated_compensation") is not None and facts.get("owner_setup_hours") is not None:
            gross = float(facts["stated_compensation"])
            cost = float(facts.get("platform_fee") or 0) + float(facts.get("required_expense") or 0)
            candidate.expected_gross_low = candidate.expected_gross_high = gross
            candidate.estimated_direct_cost = cost
            candidate.expected_net_low = candidate.expected_net_high = round(gross - cost, 2)
            candidate.setup_hours = float(facts["owner_setup_hours"])
            if facts.get("owner_recurring_hours") is not None:
                candidate.recurring_hours_per_week = float(facts["owner_recurring_hours"])
            assumptions.append("Compensation and costs are owner/source facts; acceptance probability remains unknown.")
    if assumptions:
        existing = [str(item) for item in json.loads(candidate.assumptions_json or "[]") if isinstance(item, str)]
        candidate.assumptions_json = json.dumps(list(dict.fromkeys(existing + assumptions)))
        candidate.updated_at = _now()
        session.add(candidate)
        session.commit()
        session.refresh(candidate)
    return candidate

def refresh_expired_validation_facts(session: Session, *, now: datetime | None = None) -> int:
    """Recompute candidates whose current owner facts have expired."""
    ref = now or _now()
    candidate_ids = {
        row.candidate_id
        for row in session.exec(select(OpportunityValidationFact)).all()
        if row.status == "current" and row.expires_at is not None and row.expires_at <= ref
    }
    for candidate_id in candidate_ids:
        candidate = session.get(OpportunityCandidate, candidate_id)
        if candidate is None:
            continue
        enrich_candidate_economics(session, candidate)
        qualify_candidate(session, candidate)
    ranked_candidates(session)
    return len(candidate_ids)
