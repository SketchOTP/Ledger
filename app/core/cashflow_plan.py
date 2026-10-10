"""Multi-month cashflow plan from paycheck cycles and recurring bill due dates."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from app.core.bill_schedule import bill_repeats, monthly_obligation_total
from app.core.cashflow_engine import calculate_cashflow
from app.core.credit_card_schedule import (
    cards_for_cashflow,
    monthly_credit_card_minimums_total,
)
from app.core.debt_schedule import debts_for_cashflow, monthly_debt_minimums_total
from app.core.housing_schedule import monthly_housing_payments_total
from app.core.monthly_obligations import monthly_obligations_breakdown
from app.db.models import Bill, IncomeSource

# The chart and cash-flow page expose these bounded planning horizons.  The
# longer ranges intentionally remain day-based so they use the same literal
# date/event projection as the existing 30/60/90/180-day views.
CASHFLOW_PLAN_HORIZONS: tuple[int, ...] = (30, 60, 90, 180, 182, 365, 913, 1826, 3652)


def build_cashflow_plan(session: Session, horizon_days: int = 180) -> dict[str, Any]:
    """Project cash by day using income frequency and recurring bills."""
    today = date.today()
    end = today + timedelta(days=horizon_days)
    cf = calculate_cashflow(session)

    sources = [s.model_dump() for s in session.exec(select(IncomeSource)).all()]
    bills = list(session.exec(select(Bill)).all())
    debts = debts_for_cashflow(session)
    cards = cards_for_cashflow(session)
    from app.core.budget_engine import planned_spending_daily_events
    from app.core.liquidity_planner import _apply_mortgage_grace_when_needed, _collect_events

    canonical, _diagnostics = _collect_events(session, end, as_of=today)
    _apply_mortgage_grace_when_needed(
        session, canonical, planned_spending_daily_events(session, today, end),
        float(cf["starting_cash"]), today,
    )
    events = [{
        "date": row.when.isoformat(),
        "type": "paycheck" if row.kind == "income" else row.category,
        "name": row.name, "amount": row.amount,
        "due_date": (row.due_date or row.when).isoformat(),
        "grace_used": row.grace_used,
    } for row in canonical]

    events.sort(key=lambda e: e["date"])

    # Build daily totals once.  The original implementation scanned every
    # event for every day; that is acceptable for a short outlook but makes a
    # ten-year view unnecessarily expensive as recurring events accumulate.
    income_by_date: dict[str, float] = {}
    bills_by_date: dict[str, float] = {}
    debt_by_date: dict[str, float] = {}
    mortgage_by_date: dict[str, float] = {}
    cards_by_date: dict[str, float] = {}
    for event in events:
        event_date = str(event["date"])
        amount = float(event["amount"])
        event_type = event["type"]
        if event_type == "paycheck":
            income_by_date[event_date] = income_by_date.get(event_date, 0.0) + amount
        elif event_type in {"bill", "misc", "rent"}:
            bills_by_date[event_date] = bills_by_date.get(event_date, 0.0) + amount
        elif event_type == "debt":
            debt_by_date[event_date] = debt_by_date.get(event_date, 0.0) + amount
        elif event_type == "mortgage":
            mortgage_by_date[event_date] = mortgage_by_date.get(event_date, 0.0) + amount
        elif event_type == "credit_card":
            cards_by_date[event_date] = cards_by_date.get(event_date, 0.0) + amount

    starting = float(cf.get("starting_cash", 0))
    balance = starting
    milestones: list[dict[str, Any]] = []
    daily_series: list[dict[str, Any]] = []
    lowest = {"date": today.isoformat(), "balance": balance}
    advice: list[str] = []
    repeating = [b for b in bills if bill_repeats(b.recurrence)]
    debts_missing_due = [d for d in debts if not d.due_date]
    if repeating:
        advice.append(
            f"We schedule {len(repeating)} repeating bill(s) (~${monthly_obligation_total(session):,.0f}/mo) "
            f"on their due dates through the next {horizon_days} days."
        )

    for offset in range(horizon_days + 1):
        d = today + timedelta(days=offset)
        ds = d.isoformat()
        day_in = income_by_date.get(ds, 0.0)
        day_bills = bills_by_date.get(ds, 0.0)
        day_debt = debt_by_date.get(ds, 0.0)
        day_mortgage = mortgage_by_date.get(ds, 0.0)
        day_cards = cards_by_date.get(ds, 0.0)
        day_out = day_bills + day_debt + day_mortgage + day_cards
        balance = balance + day_in - day_out
        daily_series.append(
            {
                "date": ds,
                "income": round(day_in, 2),
                "bills": round(day_bills, 2),
                "debt_minimums": round(day_debt, 2),
                "mortgage_payments": round(day_mortgage, 2),
                "outflow": round(day_out, 2),
                "net": round(day_in - day_out, 2),
                "balance": round(balance, 2),
            }
        )
        if balance < lowest["balance"]:
            lowest = {"date": ds, "balance": round(balance, 2)}
        if offset in CASHFLOW_PLAN_HORIZONS and offset <= horizon_days:
            milestones.append({"day": offset, "date": ds, "projected_balance": round(balance, 2)})

    if not sources:
        advice.append("Add your paycheck under My Money so we can forecast future pay dates.")
    if not bills:
        advice.append("Add bills and turn on “Repeats” so we know what comes back every month.")
    elif not repeating:
        advice.append("Mark bills as repeating (monthly) so we project rent, utilities, and subscriptions correctly.")
    if debts:
        advice.append(
            f"Debt minimums (~${monthly_debt_minimums_total(session):,.0f}/mo) are scheduled on due dates — "
            "you do not need to add them again under Bills."
        )
    if monthly_housing_payments_total(session) > 0:
        advice.append(
            f"Housing payments (~${monthly_housing_payments_total(session):,.0f}/mo) are scheduled "
            "separately from Bills."
        )
    if cards:
        advice.append(
            f"Credit card minimums (~${monthly_credit_card_minimums_total(session):,.0f}/mo) repeat until each balance "
            "is paid off — scheduled separately from Bills."
        )
    if debts_missing_due:
        advice.append(
            f"{len(debts_missing_due)} debt(s) have no payment due date — "
            "add their dates under Debts before relying on this forecast."
        )
    if lowest["balance"] < 0:
        advice.append(
            f"Your balance may go negative around {lowest['date']}. Pay only essentials until the next paycheck."
        )
    elif lowest["balance"] < 500:
        advice.append(
            f"Cash gets thin around {lowest['date']}. Avoid extra spending that week."
        )
    else:
        advice.append(
            "Based on your repeating bills and pay schedule, "
            "cash should cover obligations if spending stays steady."
        )

    next_pay = cf.get("next_pay_date")
    if next_pay:
        advice.insert(
            0,
            f"Next expected paycheck: {next_pay}. Bills this period: ${float(cf.get('required_bills', 0)):,.0f}; "
            f"debt: ${float(cf.get('debt_due_this_period', 0)):,.0f}; "
            f"cards: ${float(cf.get('credit_card_due_this_period', 0)):,.0f}; "
            f"mortgage: ${float(cf.get('mortgage_due_this_period', 0)):,.0f}.",
        )

    return {
        "horizon_days": horizon_days,
        "period_start": today.isoformat(),
        "period_end": end.isoformat(),
        "starting_cash": starting,
        "projected_balance_end": round(balance, 2),
        "lowest_balance": lowest,
        "milestones": milestones,
        "upcoming_events": events[:60],
        "daily_series": daily_series,
        "advice": advice,
        "monthly_bills_estimate": monthly_obligation_total(session),
        "monthly_debt_minimums": monthly_debt_minimums_total(session),
        "monthly_mortgage_payments": monthly_housing_payments_total(session),
        "monthly_obligations": monthly_obligations_breakdown(session),
        "current_cashflow": cf,
    }
