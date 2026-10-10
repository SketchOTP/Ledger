"""Debt payoff strategies: avalanche, snowball, hybrid."""

from typing import Any

from sqlmodel import Session, select

from app.db.models import Debt


def _months_to_payoff(balance: float, apr: float, payment: float) -> int:
    if payment <= 0 or balance <= 0:
        return 0
    monthly_rate = apr / 100 / 12
    if monthly_rate == 0:
        return int(balance / payment) + (1 if balance % payment else 0)
    months = 0
    b = balance
    while b > 0 and months < 600:
        interest = b * monthly_rate
        b = b + interest - payment
        months += 1
        if b >= balance and months > 1:
            return 999
    return months


def _total_interest(balance: float, apr: float, payment: float, months: int) -> float:
    monthly_rate = apr / 100 / 12
    total_interest = 0.0
    b = balance
    for _ in range(min(months, 600)):
        if b <= 0:
            break
        interest = b * monthly_rate
        total_interest += interest
        b = max(0, b + interest - payment)
    return round(total_interest, 2)


def rank_debts(session: Session, strategy: str = "avalanche", extra_payment: float = 0) -> dict[str, Any]:
    debts = list(session.exec(select(Debt)).all())
    if not debts:
        return {"strategy": strategy, "order": [], "baseline": {}, "with_extra": {}}

    if strategy == "snowball":
        ordered = sorted(debts, key=lambda d: d.balance)
    elif strategy == "hybrid":
        ordered = sorted(debts, key=lambda d: (-d.apr if d.apr > 15 else d.balance))
    else:
        ordered = sorted(debts, key=lambda d: -d.apr)

    order = []
    for i, d in enumerate(ordered):
        months = _months_to_payoff(d.balance, d.apr, d.minimum_payment)
        interest = _total_interest(d.balance, d.apr, d.minimum_payment, months)
        order.append({
            "debt_id": d.id,
            "creditor": d.creditor,
            "balance": d.balance,
            "apr": d.apr,
            "minimum_payment": d.minimum_payment,
            "rank": i + 1,
            "months_to_payoff": months,
            "total_interest_estimate": interest,
        })

    baseline_interest = sum(o["total_interest_estimate"] for o in order)
    return {
        "strategy": strategy,
        "order": order,
        "baseline": {"total_interest_estimate": baseline_interest},
        "with_extra": {"extra_payment": extra_payment, "note": "Apply extra to rank-1 debt only when cashflow safe"},
    }


def can_pay_extra_debt(cashflow: dict[str, Any]) -> bool:
    return cashflow.get("risk_level") == "safe" and cashflow.get("projected_end_balance", 0) > 0
