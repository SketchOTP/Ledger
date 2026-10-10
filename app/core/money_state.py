"""Deterministic V2 resilience reserves and deployable-capital calculation.

The calculation deliberately consumes the liquidity planner's literal-date
waterfall.  It never assigns transactions or paychecks to a budget month.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from app.core.account_balances import account_totals
from app.core.bill_schedule import _advance_months
from app.db.models import BorrowerProfile, BudgetCategory, Transaction


def _expense_history(
    session: Session, as_of: date, category_names: set[str] | None
) -> list[Transaction]:
    start = as_of - timedelta(days=90)
    if category_names == set():
        return []
    rows = session.exec(
        select(Transaction).where(
            Transaction.transaction_date >= start,
            Transaction.transaction_date < as_of,
        )
    ).all()
    if category_names is None:
        return list(rows)
    return [tx for tx in rows if str(tx.category or "").casefold() in category_names]


def _history_profile(
    session: Session, as_of: date, category_names: set[str] | None
) -> dict[str, Any]:
    """Rolling spending evidence; optionally restrict to planned categories."""
    txs = [
        t for t in _expense_history(session, as_of, category_names)
        if float(t.amount or 0) > 0
    ]
    if not txs:
        return {"count": 0, "average_30_days": 0.0, "weekly_stddev": 0.0, "spike": 0.0}

    weekly: list[float] = []
    for week in range(13):
        end = as_of - timedelta(days=week * 7)
        start = end - timedelta(days=7)
        weekly.append(
            sum(float(t.amount) for t in txs if start <= t.transaction_date < end)
        )
    active_weeks = [value for value in weekly if value > 0]
    total = sum(float(t.amount) for t in txs)
    return {
        "count": len(txs),
        "average_30_days": round(total / 90 * 30, 2),
        "weekly_stddev": round(statistics.stdev(active_weeks), 2) if len(active_weeks) >= 2 else 0.0,
        "spike": round(max(float(t.amount) for t in txs), 2),
    }


def _expected_variable_reserve(
    *, planned_variable: float, history: dict[str, Any]
) -> tuple[float, list[dict[str, Any]]]:
    planned = max(0.0, float(planned_variable or 0))
    observed = max(0.0, float(history["average_30_days"]))
    if planned > 0 and int(history["count"]) >= 6:
        value = max(planned, observed)
        source = "configured_budget_and_observed_history"
    elif planned > 0:
        value = planned
        source = "configured_budget"
    elif int(history["count"]) >= 6:
        value = observed
        source = "rolling_transaction_history"
    else:
        value = 0.0
        source = "insufficient_variable_spending_data"
    return round(value, 2), [{"source": source, "amount": round(value, 2)}]


def _uncertainty_reserve(
    *, minimum_floor: float, expected_variable: float, history: dict[str, Any]
) -> tuple[float, str, list[dict[str, Any]]]:
    floor = max(0.0, float(minimum_floor or 0))
    count = int(history["count"])
    if count < 6:
        reserve = max(floor, expected_variable * 0.25)
        return round(reserve, 2), "low", [
            {"source": "configured_minimum_floor", "amount": round(floor, 2)},
            {"source": "insufficient_history_fallback", "transaction_count": count},
        ]

    volatility = max(float(history["weekly_stddev"]) * 2, float(history["spike"]) * 0.25)
    reserve = max(floor, expected_variable * 0.15, volatility)
    return round(reserve, 2), "medium" if count < 18 else "high", [
        {"source": "configured_minimum_floor", "amount": round(floor, 2)},
        {"source": "recent_spending_volatility", "amount": round(volatility, 2)},
        {"source": "rolling_90_day_transaction_count", "count": count},
    ]


def _risk_windows(daily_series: list[dict[str, Any]], buffer: float) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for row in daily_series:
        if float(row.get("balance") or 0) < buffer:
            windows.append(
                {
                    "type": "below_resilience_floor",
                    "date": row["date"],
                    "balance": round(float(row["balance"]), 2),
                }
            )
    return windows[:12]


def _lowest_after_dated_spending(
    daily_series: list[dict[str, Any]],
    spending_schedule: list[dict[str, Any]],
    through: date,
) -> float:
    """Keep later living costs on their dates instead of charging them all today."""
    by_date: dict[str, float] = {}
    for row in spending_schedule:
        day = str(row["date"])
        by_date[day] = by_date.get(day, 0.0) + float(row["amount"])
    spent = 0.0
    lowest = float("inf")
    for row in daily_series:
        day = str(row["date"])
        if day > through.isoformat():
            break
        spent = round(spent + by_date.get(day, 0.0), 2)
        lowest = min(lowest, round(float(row["balance"]) - spent, 2))
    return round(lowest, 2) if lowest != float("inf") else 0.0


def build_money_state(
    session: Session,
    *,
    as_of: date,
    starting_cash: float,
    buffer_target: float,
    horizons: dict[int | str, dict[str, Any]],
    daily_series: list[dict[str, Any]],
    planned_variable_spending: float,
    planned_spending_schedule: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Calculate V2 capital states from one already-expanded forward timeline."""
    totals = account_totals(session)
    profile = session.exec(select(BorrowerProfile)).first()
    cushion_goal = max(0.0, float(profile.target_buffer or 0)) if profile else max(0.0, buffer_target)
    cushion_current = (
        max(0.0, float(profile.cash_cushion_balance))
        if profile and profile.cash_cushion_balance is not None else None
    )
    emergency_balance = float(totals["savings"])
    liquid_resources = max(0.0, float(starting_cash)) + max(0.0, emergency_balance)
    budget_names = {
        category.name.casefold()
        for category in session.exec(select(BudgetCategory)).all()
    }
    history = _history_profile(session, as_of, budget_names)
    uncertainty_history = _history_profile(session, as_of, None)
    expected_variable, variable_provenance = _expected_variable_reserve(
        planned_variable=planned_variable_spending, history=history
    )
    uncertainty, confidence, uncertainty_provenance = _uncertainty_reserve(
        minimum_floor=buffer_target,
        expected_variable=expected_variable,
        history=uncertainty_history,
    )

    candidates = [
        (str(key), float(payload.get("flexible_today") or 0), payload)
        for key, payload in horizons.items()
    ]
    constraining_key, timeline_headroom, constraint = min(candidates, key=lambda row: row[1])
    lowest_raw = constraint.get("lowest_balance")
    lowest_balance = float(starting_cash if lowest_raw is None else lowest_raw)
    known_obligation_reserve = max(0.0, round(float(starting_cash) - lowest_balance, 2))

    # The owner's cushion goal is not evidence of another surprise expense.
    # Only uncertainty above the already-protected floor can add a surprise holdback.
    plausible_surprise = max(
        100.0 if expected_variable > 0 else 0.0,
        max(0.0, uncertainty - float(buffer_target)) * 0.5,
    )

    # The rolling timeline already reserves the configured buffer while walking
    # every dated obligation and paycheck.  Only the portion of uncertainty
    # above that buffer is an additional holdback; dated obligations must not be
    # turned into a second emergency-fund target.
    additional_uncertainty = max(0.0, uncertainty - float(buffer_target))
    available_before_refill = max(
        0.0,
        round(timeline_headroom - expected_variable - additional_uncertainty - plausible_surprise, 2),
    )
    cushion_gap = (
        max(0.0, round(cushion_goal - cushion_current, 2))
        if cushion_current is not None else None
    )
    # Day-to-day discretion is not the same as D001 capital for a new commitment.
    # The latter keeps its conservative portfolio gate. For ordinary optional
    # spending, date the owner's living costs and protect the existing cushion
    # balance (not the entire aspirational goal) plus a near-term surprise floor.
    # The broader uncertainty reserve remains visible as a long-term target.
    planning_through = _advance_months(as_of, 6)
    planning_days = (planning_through - as_of).days
    dated_lowest = _lowest_after_dated_spending(
        daily_series, planned_spending_schedule or [], planning_through
    )
    # Day-to-day spending protects the owner's explicit emergency amount once.
    # Volatility and aspirational wealth allocations are advice, not additional
    # mandatory deductions from the same checking dollars.
    working_safety = cushion_goal
    discretionary_before_refill = max(
        0.0, round(min(float(starting_cash), dated_lowest) - working_safety, 2)
    )
    if not planned_spending_schedule or planned_variable_spending <= 0:
        discretionary_before_refill = 0.0
    # The full goal is already held above; earmarking part of it is not a
    # second expense. No automatic refill may reduce the free-to-use amount.
    cushion_refill = 0.0
    deployable_surplus = round(min(available_before_refill, discretionary_before_refill), 2)
    after_refill = max(0.0, round(discretionary_before_refill - cushion_refill, 2))
    free_share = 1.0
    discretionary_spending = after_refill
    flexible_safety = 0.0
    safety_cushion = round(
        float(buffer_target) + additional_uncertainty + plausible_surprise + cushion_refill, 2
    )
    emergency_reserve_target = round(
        cushion_goal + additional_uncertainty + plausible_surprise, 2
    )

    # Accessible checking/cash left after the timeline's bill reserve is a
    # valid emergency resource even when the household has no savings account.
    # Keep the dedicated account balance separately for auditability.
    emergency_reserve_account_balance = emergency_balance
    emergency_reserve_balance = min(
        liquid_resources,
        round(
            emergency_reserve_account_balance
            + max(0.0, timeline_headroom - expected_variable),
            2,
        ),
    )
    emergency_shortfall = max(0.0, emergency_reserve_target - emergency_reserve_balance)

    protected_components = (
        known_obligation_reserve
        + expected_variable
        + safety_cushion
    )
    protected_cash = min(liquid_resources, round(protected_components, 2))
    flexible_cash = max(0.0, round(timeline_headroom - deployable_surplus, 2))
    tolerance = (profile.risk_tolerance if profile else "moderate").lower()
    experimental_share = {"low": 0.05, "moderate": 0.10, "high": 0.15}.get(tolerance, 0.10)
    experimental_capital = round(deployable_surplus * experimental_share, 2)

    stress_reduction = round(expected_variable + safety_cushion, 2)
    reserve_total = round(
        min(
            liquid_resources,
            known_obligation_reserve + expected_variable + safety_cushion,
        ),
        2,
    )
    reserve_for_bills = min(known_obligation_reserve, liquid_resources)
    reserve_remaining = max(0.0, liquid_resources - reserve_for_bills)
    reserve_for_normal = min(expected_variable, reserve_remaining)
    reserve_remaining = max(0.0, reserve_remaining - reserve_for_normal)
    reserve_for_safety = min(safety_cushion, reserve_remaining)
    return {
        "as_of": as_of.isoformat(),
        "liquid_resources": round(liquid_resources, 2),
        "known_obligation_reserve": known_obligation_reserve,
        "expected_variable_reserve": expected_variable,
        "uncertainty_reserve": uncertainty,
        "uncertainty_confidence": confidence,
        "emergency_reserve_target": round(emergency_reserve_target, 2),
        "emergency_reserve_account_balance": round(emergency_reserve_account_balance, 2),
        "emergency_reserve_balance": round(emergency_reserve_balance, 2),
        "emergency_reserve_shortfall": round(emergency_shortfall, 2),
        "protected_cash": round(protected_cash, 2),
        "flexible_cash": flexible_cash,
        "deployable_surplus": deployable_surplus,
        "experimental_capital": experimental_capital,
        "constraining_horizon": constraining_key,
        "minimum_projected_liquidity": round(lowest_balance, 2),
        "safety_cushion": safety_cushion,
        "cash_cushion_goal": round(cushion_goal, 2),
        "cash_cushion_current": round(cushion_current, 2) if cushion_current is not None else None,
        "cash_cushion_gap": cushion_gap,
        "cash_cushion_refill": cushion_refill,
        "dated_lowest_after_normal_spending": dated_lowest,
        "working_safety_holdback": round(working_safety, 2),
        "free_to_use_now": after_refill,
        "planning_horizon_days": planning_days,
        "planning_through": planning_through.isoformat(),
        "discretionary_before_refill": round(discretionary_before_refill, 2),
        # One pool: optional saving/investing uses these same dollars.
        "total_flexible_30_days": after_refill,
        "discretionary_spending_30_days": discretionary_spending,
        "discretionary_share": round(free_share, 4),
        "flexible_safety_remaining": flexible_safety,
        "discretionary_horizon_days": planning_days,
        "extra_safety_holdback": round(additional_uncertainty + plausible_surprise, 2),
        "additional_uncertainty_reserve": round(additional_uncertainty, 2),
        "protected_cash_breakdown": {
            "for_bills": round(reserve_for_bills, 2),
            "for_normal_spending": round(reserve_for_normal, 2),
            "safety_cushion": round(reserve_for_safety, 2),
            "total": reserve_total,
        },
        "risk_windows": _risk_windows(daily_series, float(buffer_target)),
        "stress": {
            "plausible_surprise_expense": round(plausible_surprise, 2),
            "deployable_reduction_for_resilience": stress_reduction,
            "scenarios": [
                "expected_variable_spending",
                "ordinary_uncertainty",
                "moderate_unexpected_expense",
            ],
        },
        "provenance": {
            "expected_variable_reserve": variable_provenance,
            "uncertainty_reserve": uncertainty_provenance,
            "contract": "literal_date_forward_timeline",
        },
    }
