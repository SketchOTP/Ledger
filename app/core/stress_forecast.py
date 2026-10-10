"""Forward-looking cash stress forecast — deterministic, read-only DB."""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

from sqlmodel import Session

from app.config import settings
from app.core.cashflow_engine import effective_buffer_from_borrower
from app.core.db_fingerprint import capture_db_fingerprint
from app.core.financial_snapshot import FinancialStateSnapshot, build_financial_snapshot

ALLOWED_HORIZONS = frozenset({30, 60, 90, 180})
LARGE_EXPENSE_THRESHOLD = 400.0
BILL_CLUSTER_MIN_COUNT = 3
BILL_CLUSTER_WINDOW_DAYS = 5
DEBT_HEAVY_WEEK_RATIO = 0.35


def run_stress_forecast(
    session: Session,
    *,
    horizon_days: int = 90,
    starting_cash_override: float | None = None,
    planned_spending_override: float | None = None,
) -> dict[str, Any]:
    """Build forecast from live data without mutating the database."""
    snapshot = build_financial_snapshot(session)
    return build_stress_forecast(
        snapshot,
        horizon_days=horizon_days,
        starting_cash_override=starting_cash_override,
        planned_spending_override=planned_spending_override,
    )


def build_stress_forecast(
    snapshot: FinancialStateSnapshot,
    *,
    horizon_days: int = 90,
    starting_cash_override: float | None = None,
    planned_spending_override: float | None = None,
) -> dict[str, Any]:
    if horizon_days not in ALLOWED_HORIZONS:
        raise ValueError(f"horizon_days must be one of {sorted(ALLOWED_HORIZONS)}")

    cf = snapshot.cashflow
    plan = cf.get("liquidity_plan") or {}
    as_of = (plan.get("money_state") or {}).get("as_of")
    today = date.fromisoformat(as_of) if as_of else date.today()
    end = today + timedelta(days=horizon_days - 1)

    starting_cash = (
        float(starting_cash_override)
        if starting_cash_override is not None
        else float(cf.get("starting_cash", settings.starting_cash))
    )
    buffer_target = float((plan.get("buffer_target")) or effective_buffer_from_borrower(snapshot.borrower))
    variable_monthly = (
        float(planned_spending_override)
        if planned_spending_override is not None
        else float(cf.get("planned_variable_spending", 0))
    )
    variable_daily = max(0.0, variable_monthly) / max(horizon_days, 1)
    # The liquidity planner is the one canonical schedule expander.  Stress only
    # aggregates its already-expanded literal-date events and layers adversity on it.
    income_by_day: dict[str, float] = {}
    bills_by_day: dict[str, float] = {}
    debt_by_day: dict[str, float] = {}
    mortgage_by_day: dict[str, float] = {}
    large_expense_days: dict[str, float] = {}
    for event in plan.get("canonical_timeline") or []:
        ds = str(event.get("date") or "")[:10]
        if not ds or ds < today.isoformat() or ds > end.isoformat():
            continue
        amount = float(event.get("amount") or 0)
        category = event.get("category") or ""
        if event.get("kind") == "income":
            income_by_day[ds] = income_by_day.get(ds, 0.0) + amount
        elif category == "debt" or category == "credit_card":
            debt_by_day[ds] = debt_by_day.get(ds, 0.0) + amount
        else:
            if category == "mortgage":
                mortgage_by_day[ds] = mortgage_by_day.get(ds, 0.0) + amount
            else:
                bills_by_day[ds] = bills_by_day.get(ds, 0.0) + amount
            if amount >= LARGE_EXPENSE_THRESHOLD:
                large_expense_days[ds] = large_expense_days.get(ds, 0.0) + amount

    timeline: list[dict[str, Any]] = []
    balance = starting_cash
    lowest = {"date": today.isoformat(), "cash_balance": balance}
    first_shortfall: str | None = None
    first_buffer_breach: str | None = None

    for offset in range(horizon_days):
        d = today + timedelta(days=offset)
        ds = d.isoformat()
        day_start = balance
        income = round(income_by_day.get(ds, 0.0), 2)
        bills = round(bills_by_day.get(ds, 0.0) + mortgage_by_day.get(ds, 0.0), 2)
        debt_minimums = round(debt_by_day.get(ds, 0.0), 2)
        planned_spending = round(variable_daily, 2)
        events: list[str] = []

        if income > 0:
            events.append(f"Paycheck +${income:.2f}")
        if bills > 0:
            events.append(f"Bills -${bills:.2f}")
        if debt_minimums > 0:
            events.append(f"Debt minimums -${debt_minimums:.2f}")
        if ds in large_expense_days:
            events.append(f"Large upcoming expense -${large_expense_days[ds]:.2f}")
        if variable_daily > 0:
            events.append(f"Planned variable spending -${variable_daily:.2f}")

        balance = round(
            day_start + income - bills - debt_minimums - planned_spending,
            2,
        )
        if balance < lowest["cash_balance"]:
            lowest = {"date": ds, "cash_balance": balance}
        if balance < 0 and first_shortfall is None:
            first_shortfall = ds
        if balance < buffer_target and first_buffer_breach is None:
            first_buffer_breach = ds

        timeline.append({
            "date": ds,
            "starting_balance": round(day_start, 2),
            "income": income,
            "bills": bills,
            "debt_minimums": debt_minimums,
            "planned_spending": planned_spending,
            "ending_balance": balance,
            "events": events,
        })

    pay_dates = sorted(income_by_day.keys())
    risk_windows = _detect_risk_windows(
        timeline,
        pay_dates,
        buffer_target,
        debt_by_day,
        bills_by_day,
        variable_daily,
    )
    pressure_score = _pressure_score(
        first_shortfall,
        first_buffer_breach,
        lowest["cash_balance"],
        buffer_target,
        risk_windows,
        pay_dates,
    )
    status = _status_from_pressure(pressure_score)
    recommendations = _recommendations(
        status,
        first_shortfall,
        first_buffer_breach,
        lowest,
        risk_windows,
        buffer_target,
    )

    return {
        "forecast_id": str(uuid4()),
        "snapshot_id": snapshot.snapshot_id,
        "horizon_days": horizon_days,
        "starting_cash": round(starting_cash, 2),
        "ending_cash": round(balance, 2),
        "lowest_cash_day": lowest,
        "first_shortfall_date": first_shortfall,
        "first_buffer_breach_date": first_buffer_breach,
        "pressure_score": pressure_score,
        "status": status,
        "timeline": timeline,
        "risk_windows": risk_windows,
        "recommendations": recommendations,
    }


def _daily_grocery(snapshot: FinancialStateSnapshot) -> float:
    g = snapshot.groceries or {}
    stores = g.get("shopping_list_total_by_store") or []
    if stores:
        monthly = float(stores[0].get("total", 0)) * 4.33
        return max(0.0, monthly / 30.0)
    return 400.0 / 30.0


def _schedule_income(
    sources: list[dict[str, Any]],
    start: date,
    end: date,
) -> dict[str, float]:
    by_day: dict[str, float] = {}
    for src in sources:
        amount = float(src.get("amount", 0))
        freq = (src.get("pay_frequency") or "biweekly").lower()
        step = {"weekly": 7, "biweekly": 14, "monthly": 30}.get(freq, 14)
        cursor = src.get("next_pay_date")
        if isinstance(cursor, str):
            cursor = date.fromisoformat(cursor)
        if not isinstance(cursor, date):
            cursor = start + timedelta(days=step)
        while cursor < start:
            cursor += timedelta(days=step)
        while cursor <= end:
            key = cursor.isoformat()
            by_day[key] = by_day.get(key, 0.0) + amount
            cursor += timedelta(days=step)
    return by_day


def _schedule_bills(
    bills: list[dict[str, Any]],
    start: date,
    end: date,
) -> dict[str, float]:
    by_day: dict[str, float] = {}
    for bill in bills:
        if not bill.get("required", True):
            continue
        amount = float(bill.get("amount", 0))
        recurrence = (bill.get("recurrence") or "monthly").lower()
        due = bill.get("due_date")
        if isinstance(due, str):
            due = date.fromisoformat(due)
        if recurrence == "once" and isinstance(due, date) and start <= due <= end:
            key = due.isoformat()
            by_day[key] = by_day.get(key, 0.0) + amount
            continue
        if not isinstance(due, date):
            due = start.replace(day=min(28, start.day))
        for d in _dates_in_range(start, end, due, recurrence):
            key = d.isoformat()
            by_day[key] = by_day.get(key, 0.0) + amount
    return by_day


def _schedule_debt_minimums(
    debts: list[dict[str, Any]],
    start: date,
    end: date,
) -> dict[str, float]:
    by_day: dict[str, float] = {}
    for debt in debts:
        pay = float(debt.get("minimum_payment", 0))
        due = debt.get("due_date")
        if isinstance(due, str):
            due = date.fromisoformat(due)
        if not isinstance(due, date):
            due = start.replace(day=15)
        for d in _dates_in_range(start, end, due, "monthly"):
            key = d.isoformat()
            by_day[key] = by_day.get(key, 0.0) + pay
    return by_day


def _schedule_mortgages(
    mortgages: list[dict[str, Any]],
    start: date,
    end: date,
) -> dict[str, float]:
    by_day: dict[str, float] = {}
    for m in mortgages:
        pay = float(m.get("monthly_payment", 0)) + float(m.get("escrow_amount", 0))
        anchor = m.get("due_by_date") or m.get("start_date")
        if isinstance(anchor, str):
            anchor = date.fromisoformat(anchor[:10])
        if not isinstance(anchor, date):
            anchor = start.replace(day=1)
        for d in _dates_in_range(start, end, anchor, "monthly"):
            key = d.isoformat()
            by_day[key] = by_day.get(key, 0.0) + pay
    return by_day


def _large_expense_dates(
    bills: list[dict[str, Any]],
    start: date,
    end: date,
) -> dict[str, float]:
    """Flag dates with known large bills (already included in bill totals)."""
    by_day: dict[str, float] = {}
    for bill in bills:
        amount = float(bill.get("amount", 0))
        if amount < LARGE_EXPENSE_THRESHOLD:
            continue
        due = bill.get("due_date")
        if isinstance(due, str):
            due = date.fromisoformat(due)
        recurrence = (bill.get("recurrence") or "monthly").lower()
        if recurrence == "once" and isinstance(due, date) and start <= due <= end:
            by_day[due.isoformat()] = by_day.get(due.isoformat(), 0.0) + amount
        elif isinstance(due, date):
            for d in _dates_in_range(start, end, due, recurrence):
                key = d.isoformat()
                by_day[key] = by_day.get(key, 0.0) + amount
    return by_day


def _dates_in_range(start: date, end: date, anchor: date, recurrence: str) -> list[date]:
    out: list[date] = []
    if recurrence == "weekly":
        cursor = anchor
        while cursor < start:
            cursor += timedelta(days=7)
        while cursor <= end:
            out.append(cursor)
            cursor += timedelta(days=7)
        return out

    cursor = anchor
    while cursor < start:
        cursor = _advance_monthly(cursor)
    while cursor <= end:
        out.append(cursor)
        cursor = _advance_monthly(cursor)
    return out


def _advance_monthly(d: date) -> date:
    month = d.month % 12 + 1
    year = d.year + (1 if d.month == 12 else 0)
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _detect_risk_windows(
    timeline: list[dict[str, Any]],
    pay_dates: list[str],
    buffer_target: float,
    debt_by_day: dict[str, float],
    bills_by_day: dict[str, float],
    grocery_daily: float,
) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []

    neg_start: str | None = None
    buf_start: str | None = None
    prev_date: str | None = None
    for row in timeline:
        ds = row["date"]
        bal = float(row["ending_balance"])
        if bal < 0:
            if neg_start is None:
                neg_start = ds
        elif neg_start and prev_date:
            windows.append({
                "type": "negative_balance_window",
                "start": neg_start,
                "end": prev_date,
                "severity": "critical",
                "message": "Cash balance projected below zero",
            })
            neg_start = None
        if bal < buffer_target:
            if buf_start is None:
                buf_start = ds
        elif buf_start and prev_date:
            windows.append({
                "type": "below_buffer_window",
                "start": buf_start,
                "end": prev_date,
                "severity": "warning",
                "message": f"Balance below emergency buffer (${buffer_target:.0f})",
            })
            buf_start = None
        prev_date = ds

    if neg_start:
        windows.append({
            "type": "negative_balance_window",
            "start": neg_start,
            "end": timeline[-1]["date"],
            "severity": "critical",
            "message": "Cash balance projected below zero",
        })
    if buf_start:
        windows.append({
            "type": "below_buffer_window",
            "start": buf_start,
            "end": timeline[-1]["date"],
            "severity": "warning",
            "message": f"Balance below emergency buffer (${buffer_target:.0f})",
        })

    for pay in pay_dates:
        pay_d = date.fromisoformat(pay)
        cluster_start = pay_d - timedelta(days=BILL_CLUSTER_WINDOW_DAYS)
        cluster_bills = 0
        cluster_total = 0.0
        for row in timeline:
            d = date.fromisoformat(row["date"])
            if cluster_start <= d < pay_d:
                b = float(bills_by_day.get(row["date"], 0))
                if b > 0:
                    cluster_bills += 1
                    cluster_total += b
        if cluster_bills >= BILL_CLUSTER_MIN_COUNT:
            windows.append({
                "type": "bill_cluster_before_paycheck",
                "start": cluster_start.isoformat(),
                "end": (pay_d - timedelta(days=1)).isoformat(),
                "severity": "warning",
                "message": (
                    f"{cluster_bills} bills (${cluster_total:.0f}) due within "
                    f"{BILL_CLUSTER_WINDOW_DAYS} days before paycheck"
                ),
            })

    for i in range(0, max(0, len(timeline) - 6)):
        chunk = timeline[i : i + 7]
        debt_sum = sum(float(r["debt_minimums"]) for r in chunk)
        income_sum = sum(float(r["income"]) for r in chunk)
        if debt_sum > 0 and (income_sum == 0 or debt_sum / max(income_sum, 1) >= DEBT_HEAVY_WEEK_RATIO):
            if debt_sum >= 50:
                windows.append({
                    "type": "debt_heavy_week",
                    "start": chunk[0]["date"],
                    "end": chunk[-1]["date"],
                    "severity": "warning",
                    "message": f"Debt minimums ${debt_sum:.0f} dominate this week",
                })
                break

    for i in range(0, max(0, len(timeline) - 6)):
        chunk = timeline[i : i + 7]
        spend = sum(float(r["planned_spending"]) for r in chunk)
        min_bal = min(float(r["ending_balance"]) for r in chunk)
        if spend > grocery_daily * 7 * 1.2 and min_bal < buffer_target * 0.75:
            windows.append({
                "type": "grocery_squeeze_week",
                "start": chunk[0]["date"],
                "end": chunk[-1]["date"],
                "severity": "warning",
                "message": "High planned spending while balance is tight",
            })
            break

    return _dedupe_windows(windows)


def _dedupe_windows(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for w in windows:
        key = (w["type"], w["start"], w["end"])
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
    return out


def _max_income_gap_days(pay_dates: list[str]) -> int:
    if len(pay_dates) < 2:
        return 0
    dates = sorted(date.fromisoformat(p) for p in pay_dates)
    return max((dates[i + 1] - dates[i]).days for i in range(len(dates) - 1))


def _pressure_score(
    first_shortfall: str | None,
    first_buffer_breach: str | None,
    lowest_cash: float,
    buffer_target: float,
    risk_windows: list[dict[str, Any]],
    pay_dates: list[str],
) -> int:
    score = 0
    if first_shortfall:
        score += 40
    if first_buffer_breach:
        score += 25
    if lowest_cash < buffer_target * 0.5:
        score += 15
    if any(w["type"] == "debt_heavy_week" for w in risk_windows):
        score += 10
    if _max_income_gap_days(pay_dates) > 14:
        score += 10
    if any(w["type"] == "bill_cluster_before_paycheck" for w in risk_windows):
        score += 10
    return min(100, score)


def _status_from_pressure(score: int) -> str:
    if score <= 24:
        return "safe"
    if score <= 49:
        return "watch"
    if score <= 74:
        return "exposed"
    return "danger"


def _recommendations(
    status: str,
    first_shortfall: str | None,
    first_buffer_breach: str | None,
    lowest: dict[str, Any],
    risk_windows: list[dict[str, Any]],
    buffer_target: float,
) -> list[str]:
    recs: list[str] = []
    if first_shortfall:
        recs.append(
            f"Before {first_shortfall}, reduce discretionary spending or move a bill — "
            "projected balance goes negative."
        )
    if first_buffer_breach and not first_shortfall:
        recs.append(
            f"Buffer breach expected by {first_buffer_breach}; aim to keep "
            f"${buffer_target:.0f} available."
        )
    if lowest["cash_balance"] < buffer_target:
        recs.append(
            f"Lowest projected balance ${lowest['cash_balance']:.2f} on {lowest['date']} — "
            "review bills due that week."
        )
    for w in risk_windows:
        if w["type"] == "bill_cluster_before_paycheck":
            recs.append(
                f"Stagger or prepay bills between {w['start']} and {w['end']} "
                "to avoid a pre-paycheck crunch."
            )
            break
    if status in ("exposed", "danger"):
        recs.append("Pause extra debt payments until cashflow stabilizes.")
    if not recs:
        recs.append("No immediate exposure detected — maintain current buffer and bill schedule.")
    return recs[:6]


__all__ = [
    "ALLOWED_HORIZONS",
    "build_stress_forecast",
    "capture_db_fingerprint",
    "run_stress_forecast",
]
