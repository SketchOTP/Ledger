"""Credit card payoff and balance transfer analysis."""

from typing import Any

from app.db.models import CreditCard


def analyze_credit_card(card: CreditCard, market_bt_apr: float | None = None) -> dict[str, Any]:
    util = card.utilization_percent
    if util is None and card.credit_limit > 0:
        util = round(card.balance / card.credit_limit * 100, 1)

    risk_flags: list[str] = []
    if util and util > 30:
        risk_flags.append("High utilization hurts credit score")
    if card.apr >= 20:
        risk_flags.append("High APR — interest costly")

    annual_interest = card.balance * (card.apr / 100)
    better_options: list[dict[str, Any]] = []
    bt_candidate = False
    loan_candidate = False

    if market_bt_apr is not None and market_bt_apr < card.apr:
        fee = card.balance * 0.03
        savings = annual_interest - card.balance * (market_bt_apr / 100)
        if savings > fee:
            bt_candidate = True
            better_options.append({
                "type": "balance_transfer",
                "estimated_apr": market_bt_apr,
                "fee_estimate": round(fee, 2),
                "annual_savings_estimate": round(savings - fee, 2),
            })

    recommended = "Pay minimum and prioritize in debt avalanche"
    if bt_candidate:
        recommended = "Compare balance transfer offers (verify fees and promo period)"
    elif card.apr >= 25:
        recommended = "Consider hardship program contact before balance transfer"

    return {
        "card_id": card.id,
        "current_card_cost": f"${annual_interest:.0f}/yr estimated interest at current APR",
        "better_options": better_options,
        "balance_transfer_candidate": bt_candidate,
        "personal_loan_candidate": loan_candidate,
        "risk_flags": risk_flags,
        "recommended_action": recommended,
        "utilization_percent": util,
    }
