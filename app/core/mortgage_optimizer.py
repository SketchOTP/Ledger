"""Mortgage refinance analysis — deterministic only."""

from typing import Any

from app.core.mortgage_schedule import mortgage_schedule_meta
from app.db.models import MortgageAccount


def analyze_mortgage(
    mortgage: MortgageAccount,
    estimated_market_rate: float | None = None,
    closing_cost_assumption: float = 5000.0,
) -> dict[str, Any]:
    """Analyze refinance candidacy. No fabricated market rates unless provided."""
    current_rate = mortgage.interest_rate
    market_rate = estimated_market_rate
    risks: list[str] = []

    if market_rate is None:
        return _analysis_with_schedule(
            mortgage,
            {
                "current_rate": current_rate,
                "estimated_market_rate": None,
                "monthly_savings_estimate": 0,
                "break_even_months": 0,
                "closing_cost_assumption": closing_cost_assumption,
                "recommendation": "monitor",
                "why": "No market rate data stored; enter estimated_market_rate or run research plugin",
                "risks": ["Market rate unknown — manual verification required"],
            },
        )

    if market_rate >= current_rate:
        return _analysis_with_schedule(
            mortgage,
            {
                "current_rate": current_rate,
                "estimated_market_rate": market_rate,
                "monthly_savings_estimate": 0,
                "break_even_months": 0,
                "closing_cost_assumption": closing_cost_assumption,
                "recommendation": "ignore",
                "why": "Current rate is at or below estimated market rate",
                "risks": [],
            },
        )

    balance = mortgage.current_balance
    term_months = mortgage.remaining_term_months or (mortgage.term_years * 12)
    r_old = current_rate / 100 / 12
    r_new = market_rate / 100 / 12
    n = term_months

    if r_old > 0:
        old_pay = balance * (r_old * (1 + r_old) ** n) / ((1 + r_old) ** n - 1) if n > 0 else 0
    else:
        old_pay = balance / n if n else 0
    if r_new > 0:
        new_pay = balance * (r_new * (1 + r_new) ** n) / ((1 + r_new) ** n - 1) if n > 0 else 0
    else:
        new_pay = balance / n if n else 0

    monthly_savings = max(0, old_pay - new_pay)
    break_even = int(closing_cost_assumption / monthly_savings) if monthly_savings > 0 else 999

    if monthly_savings <= 0:
        rec = "ignore"
        why = "No monthly savings at estimated market rate"
    elif break_even > 60:
        rec = "monitor"
        why = f"Break-even {break_even} months exceeds typical refinance window"
        risks.append("Long break-even period")
    elif closing_cost_assumption > monthly_savings * 24:
        rec = "monitor"
        why = "Closing costs may exceed realistic short-term savings"
    else:
        rec = "compare_lenders"
        why = f"Potential ${monthly_savings:.0f}/mo savings; break-even ~{break_even} months"

    if term_months > 360 and market_rate < current_rate:
        risks.append("Extending term increases total interest — verify loan term reset")

    return _analysis_with_schedule(
        mortgage,
        {
            "current_rate": current_rate,
            "estimated_market_rate": market_rate,
            "monthly_savings_estimate": round(monthly_savings, 2),
            "break_even_months": break_even,
            "closing_cost_assumption": closing_cost_assumption,
            "recommendation": rec,
            "why": why,
            "risks": risks,
        },
    )


def _analysis_with_schedule(mortgage: MortgageAccount, payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    out["payment_schedule"] = mortgage_schedule_meta(mortgage)
    return out
