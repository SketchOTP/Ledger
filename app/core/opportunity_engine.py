"""Financial opportunity detection — deterministic money leak / trap finder."""

from typing import Any

from sqlmodel import Session

from app.core.financial_snapshot import FinancialStateSnapshot, detect_recurring_subscriptions


def detect_opportunities(snapshot: FinancialStateSnapshot, session: Session | None = None) -> list[dict[str, Any]]:
    """
    Constantly answer: where is money leaking, trapped, overpriced, at risk, or underutilized?
    All logic is deterministic; AI only explains results later.
    """
    opportunities: list[dict[str, Any]] = []

    opportunities.extend(_mortgage_opportunities(snapshot))
    opportunities.extend(_credit_card_opportunities(snapshot, session))
    if session is not None:
        opportunities.extend(_debt_opportunities(snapshot, session))
    opportunities.extend(_insurance_opportunities(snapshot))
    opportunities.extend(_cash_opportunities(snapshot))
    opportunities.extend(_research_opportunities(snapshot))

    if session is not None:
        opportunities.extend(_subscription_opportunities(session))

    return sorted(opportunities, key=lambda o: -o.get("scores", {}).get("overall_priority_score", 0))


def _opp(
    category: str,
    title: str,
    leak_type: str,
    why: str,
    math: dict[str, Any],
    monthly_savings: float = 0,
    yearly_savings: float = 0,
    confidence: float = 0.7,
    approval_odds: str = "unknown",
    sources: list[str] | None = None,
    warnings: list[str] | None = None,
    engines: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "category": category,
        "title": title,
        "leak_type": leak_type,
        "why": why,
        "math": math,
        "estimated_monthly_savings": round(monthly_savings, 2),
        "estimated_yearly_savings": round(yearly_savings or monthly_savings * 12, 2),
        "confidence": confidence,
        "approval_odds": approval_odds,
        "sources": sources or [],
        "warnings": warnings or [],
        "engines_involved": engines or ["opportunity_engine"],
        "requires_manual_verification": confidence < 0.75,
    }


def _mortgage_opportunities(snapshot: FinancialStateSnapshot) -> list[dict[str, Any]]:
    out = []
    market_rate = None
    for o in snapshot.research:
        if o.get("product_type") in ("mortgage_refinance", "mortgage_rate") and o.get("estimated_apr"):
            market_rate = float(o["estimated_apr"])
            break

    for m_raw, analysis in zip(snapshot.raw_mortgages, snapshot.mortgage, strict=False):
        current = float(m_raw.get("interest_rate", 0))
        if market_rate and current > market_rate:
            monthly = float(analysis.get("monthly_savings_estimate", 0))
            be = analysis.get("break_even_months", 0)
            out.append(
                _opp(
                    "mortgage",
                    f"Refinance monitor: {m_raw.get('lender', 'mortgage')}",
                    "overpriced_debt",
                    (
                        f"Current rate {current}% vs market comparable {market_rate}%. "
                        f"Potential ~${monthly:.0f}/mo savings; break-even ~{be} months."
                    ),
                    {
                        "current_rate": current,
                        "market_comparable": market_rate,
                        "monthly_savings_estimate": monthly,
                        "break_even_months": be,
                        **analysis.get("math", analysis),
                    },
                    monthly_savings=monthly,
                    confidence=0.65 if analysis.get("recommendation") == "compare_lenders" else 0.5,
                    approval_odds="possible",
                    sources=[o.get("source_url", "") for o in snapshot.research if o.get("source_url")][:1],
                    warnings=analysis.get("risks", []),
                    engines=["mortgage_optimizer", "fred_mortgage"],
                )
            )
    return out


def _credit_card_opportunities(
    snapshot: FinancialStateSnapshot, session: Session | None = None
) -> list[dict[str, Any]]:
    out = []
    for card, analysis in zip(snapshot.raw_credit_cards, snapshot.credit, strict=False):
        apr = float(card.get("apr", 0))
        balance = float(card.get("balance", 0))
        if apr >= 20 and balance > 0:
            yearly_interest = _debt_interest(balance, apr, float(card.get("minimum_payment") or 0))[0]
            out.append(
                _opp(
                    "credit_card",
                    f"High-APR card: {card.get('issuer', 'card')}",
                    "expensive_debt",
                    f"APR {apr}% on ${balance:.0f} balance — ~${yearly_interest:.0f}/yr interest cost",
                    {
                        "apr": apr,
                        "balance": balance,
                        "yearly_interest_estimate": round(yearly_interest, 2),
                        "balance_transfer_candidate": analysis.get("balance_transfer_candidate"),
                        **{k: v for k, v in analysis.items() if k != "recommended_action"},
                    },
                    # A high APR establishes an interest cost, not a guaranteed
                    # savings percentage.  Without a verified alternative,
                    # keep the opportunity as a validation prompt with unknown
                    # economics rather than inventing a 30% reduction.
                    yearly_savings=0,
                    confidence=0.8,
                    approval_odds="possible" if snapshot.dti.get("status") != "high-risk" else "weak",
                    engines=["credit_card_optimizer"],
                )
            )
        for offer in snapshot.research:
            bt_math = offer.get("math", {})
            if offer.get("product_type") == "balance_transfer" and bt_math.get("card_id") == card.get("id"):
                math = offer["math"]
                out.append(
                    _opp(
                        "credit_card",
                        f"Balance transfer candidate: {card.get('issuer')}",
                        "debt_restructure",
                        offer.get("eligibility_notes", ""),
                        math,
                        yearly_savings=float(math.get("net_first_year_savings", 0)),
                        confidence=float(offer.get("confidence", 0.5)),
                        approval_odds="possible",
                        sources=[offer.get("source_url", "")],
                        warnings=offer.get("warnings", []),
                        engines=["curated_credit_benchmark"],
                    )
                )
    return out


def _debt_amortization(
    balance: float,
    apr: float,
    minimum_payment: float,
    *,
    extra: float = 0.0,
) -> dict[str, Any]:
    """Simulate declining balances and cumulative interest at real horizons."""
    principal = max(0.0, balance - max(0.0, extra))
    payment = max(0.0, minimum_payment)
    monthly_rate = max(0.0, apr) / 100 / 12
    if principal <= 0:
        return {
            "interest_by_days": {30: 0.0, 60: 0.0, 90: 0.0, 180: 0.0, 365: 0.0},
            "lifetime_interest": 0.0,
            "payoff_months": 0,
        }
    if payment <= principal * monthly_rate:
        return {
            "interest_by_days": {
                30: float("inf"),
                60: float("inf"),
                90: float("inf"),
                180: float("inf"),
                365: float("inf"),
            },
            "lifetime_interest": float("inf"),
            "payoff_months": 600,
        }
    interest = 0.0
    months = 0
    horizons = {30: None, 60: None, 90: None, 180: None, 365: None}
    while principal > 0.005 and months < 600:
        charge = principal * monthly_rate
        interest += charge
        principal = max(0.0, principal + charge - min(payment, principal + charge))
        months += 1
        for days in horizons:
            target_month = max(1, round(days / 30.4375))
            if horizons[days] is None and (months >= target_month or principal <= 0.005):
                horizons[days] = round(interest, 2)
    for days in horizons:
        if horizons[days] is None:
            horizons[days] = round(interest, 2)
    return {
        "interest_by_days": {days: float(value) for days, value in horizons.items()},
        "lifetime_interest": round(interest, 2),
        "payoff_months": months,
    }


def _debt_interest(balance: float, apr: float, minimum_payment: float, *, extra: float = 0.0) -> tuple[float, int]:
    """Backward-compatible lifetime interest/payoff tuple."""
    result = _debt_amortization(balance, apr, minimum_payment, extra=extra)
    return float(result["lifetime_interest"]), int(result["payoff_months"])


def _native_debt_payment_opportunity(
    snapshot: FinancialStateSnapshot, card: dict[str, Any], session: Session | None
) -> dict[str, Any] | None:
    """Build a household-native, deterministic extra-payment opportunity."""
    money = snapshot.money_state or snapshot.cashflow.get("money_state") or {}
    deployable = max(0.0, float(money.get("deployable_surplus") or 0))
    balance = max(0.0, float(card.get("balance") or 0))
    extra = round(min(deployable, balance), 2)
    minimum = float(card.get("minimum_payment") or 0)
    apr = float(card.get("apr") or 0)
    if extra <= 0 or minimum <= 0 or apr <= 0:
        return None
    baseline = _debt_amortization(balance, apr, minimum)
    accelerated = _debt_amortization(balance, apr, minimum, extra=extra)
    if not (baseline["lifetime_interest"] < float("inf") and accelerated["lifetime_interest"] < float("inf")):
        return None
    horizon_values = {
        days: round(max(0.0, baseline["interest_by_days"][days] - accelerated["interest_by_days"][days]), 2)
        for days in (30, 60, 90, 180, 365)
    }
    lifetime_avoided = round(max(0.0, baseline["lifetime_interest"] - accelerated["lifetime_interest"]), 2)
    value_365 = horizon_values[365]
    if value_365 <= 0:
        return None
    if session is not None:
        from sqlmodel import select

        from app.db.models import CapabilityProfile

        profile = session.exec(select(CapabilityProfile)).first()
        threshold = float(profile.minimum_meaningful_payoff or 0) if profile else 0.0
        if threshold > 0 and value_365 < threshold:
            return None
    card_id = str(card.get("id") or card.get("card_name") or card.get("issuer") or "card")
    return _opp(
        "debt_paydown",
        "Pay extra toward high-interest debt",
        "expensive_debt",
        "A safe extra payment would reduce the interest this debt accrues.",
        {
            "debt_id": card_id,
            "balance": balance,
            "apr": apr,
            "minimum_payment": minimum,
            "max_safe_extra_payment": extra,
            "interest_avoided": lifetime_avoided,
            "interest_avoided_lifetime": lifetime_avoided,
            "payoff_acceleration_months": max(0, baseline["payoff_months"] - accelerated["payoff_months"]),
            "baseline_payoff_months": baseline["payoff_months"],
            "accelerated_payoff_months": accelerated["payoff_months"],
            "value_30_days": horizon_values[30],
            "value_60_days": horizon_values[60],
            "value_90_days": horizon_values[90],
            "value_180_days": horizon_values[180],
            "value_six_months": horizon_values[180],
            "value_365_days": value_365,
            "value_horizon_days": 365,
            "payment_semantics": (
                "stated current minimum payment held constant; issuer future minimum formula not modeled"
            ),
            "provenance": "canonical_debt_and_d001_money_state",
        },
        yearly_savings=value_365,
        confidence=1.0,
        engines=["debt_amortization", "d001_money_state"],
    ) | {"canonical_identity": f"debt_extra_payment:{card_id}"}


def _debt_opportunities(snapshot: FinancialStateSnapshot, session: Session) -> list[dict[str, Any]]:
    """Select one strongest native target across the complete debt universe."""
    universe: list[dict[str, Any]] = []
    seen: set[tuple[str, float, float, float]] = set()
    for row in [*snapshot.raw_credit_cards, *snapshot.raw_debts]:
        key = (
            str(row.get("creditor") or row.get("issuer") or "").strip().lower(),
            round(float(row.get("balance") or 0), 2),
            round(float(row.get("apr") or 0), 4),
            round(float(row.get("minimum_payment") or 0), 2),
        )
        if key in seen:
            continue
        seen.add(key)
        if float(row.get("apr") or 0) >= 20:
            universe.append(row)
    native = [
        item for row in universe if (item := _native_debt_payment_opportunity(snapshot, row, session)) is not None
    ]
    return [max(native, key=lambda item: item["math"].get("value_365_days", 0))] if native else []


def _insurance_opportunities(snapshot: FinancialStateSnapshot) -> list[dict[str, Any]]:
    out = []
    for offer in snapshot.research:
        if offer.get("product_type") == "insurance" and float(offer.get("estimated_savings", 0)) > 0:
            math = offer.get("math", {})
            out.append(
                _opp(
                    "insurance",
                    "Insurance may be above median",
                    "recurring_overspend",
                    offer.get("eligibility_notes", ""),
                    math,
                    monthly_savings=float(offer.get("estimated_savings", 0)) / 12,
                    confidence=float(offer.get("confidence", 0.4)),
                    approval_odds="strong",
                    sources=[offer.get("source_url", "")],
                    warnings=offer.get("warnings", []),
                    engines=["insurance_benchmark"],
                )
            )
    return out


def _cash_opportunities(snapshot: FinancialStateSnapshot) -> list[dict[str, Any]]:
    out = []
    for offer in snapshot.research:
        if offer.get("product_type") == "hysa" and float(offer.get("estimated_apr", 0)) > 0:
            out.append(
                _opp(
                    "savings",
                    f"HYSA rate available: {offer.get('provider')}",
                    "cash_underutilized",
                    offer.get("eligibility_notes", ""),
                    offer.get("math", {}),
                    yearly_savings=float(offer.get("estimated_savings", 0)),
                    confidence=float(offer.get("confidence", 0.6)),
                    sources=[offer.get("source_url", "")],
                    warnings=offer.get("warnings", []),
                    engines=["curated_hysa"],
                )
            )
    return out


def _research_opportunities(snapshot: FinancialStateSnapshot) -> list[dict[str, Any]]:
    return []


def _subscription_opportunities(session: Session) -> list[dict[str, Any]]:
    leaks = detect_recurring_subscriptions(session)
    opportunities = []
    for leak in leaks[:5]:
        opportunity = _opp(
            "subscription",
            f"Recurring charge: {leak['description']}",
            "subscription_leak",
            f"{leak['occurrences']} similar charges, ~${leak['avg_amount']:.2f} each, {leak['cadence']}",
            leak,
            # Observed recurring spend is a maximum avoidable cost only.  The
            # owner must confirm cancellation/downgrade before any realized
            # value can be estimated.
            monthly_savings=0,
            confidence=0.6,
            warnings=["Verify before canceling — may not be subscription"],
            engines=["subscription_scanner"],
        )
        opportunity["canonical_identity"] = leak["canonical_identity"]
        opportunity["math"] = leak
        opportunities.append(opportunity)
    return opportunities
