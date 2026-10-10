"""Read-only, conditional ways to bridge a dated cash shortfall.

These simulations do not change an obligation's contractual due date.  A move
is never included in the canonical financial plan until the owner updates the
underlying record after confirming the terms with the payee.
"""

from __future__ import annotations

from datetime import date, timedelta
from itertools import combinations
from typing import Any


def _ordered(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        events,
        key=lambda row: (row["date"], 0 if row["kind"] == "income" else 1, row["name"]),
    )


def _lowest_before(
    cash: float, events: list[dict[str, Any]], through: date, *, holdback: float = 0
) -> float:
    balance = cash - holdback
    lowest = balance
    for row in _ordered(events):
        if date.fromisoformat(row["date"]) > through:
            break
        amount = float(row["amount"])
        balance += amount if row["kind"] == "income" else -amount
        lowest = min(lowest, balance)
    return round(lowest, 2)


def build_cash_rescue_plan(
    *,
    cash: float,
    events: list[dict[str, Any]],
    as_of: date,
    normal_spending_reserve: float,
    normal_spending_schedule: list[dict[str, Any]] | None = None,
    normal_spending_entered: bool,
    cushion_target: float,
    cushion_configured: bool | None = None,
    horizon_days: int = 30,
) -> dict[str, Any]:
    """Compare the unchanged timeline with bounded, *unapproved* date moves.

    When a dated living-spending schedule is available, accrue its estimated
    daily amounts instead of treating the whole month as spent before payday.
    Standalone callers can still pass a single conservative holdback.
    """
    end = as_of + timedelta(days=max(30, horizon_days))
    scheduled_living = normal_spending_schedule or []
    current = [
        row for row in _ordered(events + scheduled_living)
        if date.fromisoformat(row["date"]) <= end
    ]
    holdback = 0.0 if scheduled_living else max(0.0, normal_spending_reserve)
    balance = round(cash - holdback, 2)
    first_gap: dict[str, Any] | None = (
        {"date": as_of.isoformat(), "payment": "Current checking balance", "amount": round(-balance, 2)}
        if balance < 0 else None
    )
    for row in current:
        amount = float(row["amount"])
        balance = round(balance + (amount if row["kind"] == "income" else -amount), 2)
        if balance < 0 and first_gap is None:
            first_gap = {
                "date": row["date"],
                "payment": row["name"] if row["kind"] == "obligation" else None,
                "amount": round(-balance, 2),
            }

    lowest_30 = _lowest_before(cash, current, end, holdback=holdback)
    gap_depends_on_living_holdback = (
        first_gap is not None
        and (holdback > 0 or bool(scheduled_living))
        and _lowest_before(
            cash, [row for row in current if row["category"] != "living"], end
        ) >= 0
    )
    result: dict[str, Any] = {
        "first_gap": first_gap,
        "gap_depends_on_living_holdback": gap_depends_on_living_holdback,
        "lowest_30_day_balance": lowest_30,
        "normal_spending_entered": normal_spending_entered,
        "normal_spending_held": round(
            sum(float(row["amount"]) for row in scheduled_living) or holdback, 2
        ),
        "cushion_target": round(max(0.0, cushion_target), 2),
        "cushion_configured": (
            cushion_target > 0 if cushion_configured is None else cushion_configured
        ),
        "cushion_gap": round(max(0.0, cushion_target - lowest_30), 2),
        "next_income": None,
        "needed_before_income": round(max(0.0, -lowest_30), 2),
        "scenarios": [],
        "conditional_only": True,
        "planning_horizon_days": max(30, horizon_days),
    }
    if first_gap is None:
        return result

    gap_day = date.fromisoformat(first_gap["date"])
    next_income = next(
        (
            row for row in current
            if row["kind"] == "income" and date.fromisoformat(row["date"]) > gap_day
        ),
        None,
    )
    if next_income is None:
        return result

    income_day = date.fromisoformat(next_income["date"])
    before_income = income_day - timedelta(days=1)
    needed = max(0.0, -_lowest_before(cash, current, before_income, holdback=holdback))
    result["next_income"] = {"date": next_income["date"], "amount": next_income["amount"]}
    result["needed_before_income"] = round(needed, 2)

    def scenario(indices: tuple[int, ...]) -> dict[str, Any] | None:
        # Never erase a payment. Move only the selected occurrences, then
        # recompute the dated timeline with every original amount still owed.
        changed = [dict(item) for item in current]
        selected = [current[index] for index in indices]
        for index in indices:
            changed[index]["date"] = income_day.isoformat()
        remaining = max(0.0, -_lowest_before(cash, changed, before_income, holdback=holdback))
        if remaining >= needed:
            return None
        later_gap = max(0.0, -_lowest_before(cash, changed, end, holdback=holdback))
        return {
            "payment": " + ".join(row["name"] for row in selected),
            "original_date": min(row["date"] for row in selected),
            "proposed_date": income_day.isoformat(),
            "amount": round(sum(float(row["amount"]) for row in selected), 2),
            "changes": [
                {
                    "payment": row["name"], "original_date": row["date"],
                    "amount": round(float(row["amount"]), 2),
                }
                for row in selected
            ],
            "remaining_gap_before_income": round(remaining, 2),
            "remaining_gap_30_days": round(later_gap, 2),
            "closes_recorded_gap": remaining == 0 and later_gap == 0,
            "requires_payee_confirmation": True,
        }

    eligible = [
        index for index, row in enumerate(current)
        if row["kind"] == "obligation" and row["category"] != "living"
        and not row.get("deferred_once")
        and date.fromisoformat(row["date"]) <= before_income
    ]
    if len(eligible) > 12:
        eligible = eligible[:6] + eligible[-6:]
    scenarios = [
        proposal for size in (1, 2) for indices in combinations(eligible, size)
        if (proposal := scenario(indices)) is not None
    ]
    scenarios.sort(
        key=lambda row: (
            row["remaining_gap_before_income"], row["remaining_gap_30_days"],
            len(row["changes"]),
            row["amount"], row["original_date"], row["payment"],
        )
    )
    result["scenarios"] = scenarios[:3]
    return result
