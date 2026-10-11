"""Budget category tracking for owner-entered everyday spending."""

import calendar
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, or_
from sqlmodel import Session, select

from app.db.models import BorrowerProfile, BudgetCategory, Transaction


def check_budget_exceeded(session: Session) -> list[dict[str, Any]]:
    exceeded = []
    categories = session.exec(select(BudgetCategory)).all()
    for cat in categories:
        limit = budget_for_month(cat, date.today().year, date.today().month)
        if cat.spent_this_period > limit:
            exceeded.append({
                "category_id": cat.id,
                "name": cat.name,
                "limit": limit,
                "spent": cat.spent_this_period,
                "over_by": round(cat.spent_this_period - limit, 2),
            })
    return exceeded


def budget_for_month(category: BudgetCategory, year: int, month: int) -> float:
    """Translate an owner's stated daily/weekly/monthly habit into this calendar month."""
    amount = category.usual_amount
    if amount is None:
        return max(0.0, float(category.monthly_limit))
    days = calendar.monthrange(year, month)[1]
    if category.period == "day":
        return round(max(0.0, amount) * days, 2)
    if category.period == "week":
        return round(max(0.0, amount) * days / 7, 2)
    return round(max(0.0, amount), 2)


def remaining_monthly_budget(session: Session, as_of: date) -> float:
    """Estimate spending still ahead in this month from the owner's cadence.

    A daily or weekly *usual amount* is a forward rate, not an unspent monthly
    envelope.  Treating the whole converted month as unspent late in the month
    incorrectly front-loads weeks of past living costs into the next payday.
    Legacy/monthly amounts remain envelopes reduced by recorded transactions.
    """
    categories = list(session.exec(select(BudgetCategory)).all())
    if not categories:
        return 0.0
    spent = {category.name.casefold(): 0.0 for category in categories}
    transactions = session.exec(
        select(Transaction).where(
            Transaction.transaction_date >= as_of.replace(day=1),
            Transaction.transaction_date <= as_of,
            Transaction.amount > 0,
        )
    ).all()
    for transaction in transactions:
        key = transaction.category.casefold()
        if key in spent:
            spent[key] += float(transaction.amount)
    days_left = calendar.monthrange(as_of.year, as_of.month)[1] - as_of.day + 1
    total = 0.0
    for category in categories:
        if category.usual_amount is not None and category.period in {"day", "week"}:
            divisor = 1 if category.period == "day" else 7
            total += max(0.0, float(category.usual_amount)) * days_left / divisor
        else:
            total += max(
                0.0,
                budget_for_month(category, as_of.year, as_of.month)
                - spent[category.name.casefold()],
            )
    return round(total, 2)


def planned_spending_in_window(session: Session, start: date, end: date) -> float:
    """Use the same dated spending as the payment plan, including grocery trips."""
    return round(sum(row["amount"] for row in planned_spending_daily_events(session, start, end)), 2)


def _is_weekly_groceries(category: BudgetCategory) -> bool:
    return (category.period == "week" and category.usual_amount is not None
            and category.name.strip().casefold() in {"grocery", "groceries"})


def spending_attention(session: Session, as_of: date) -> dict[str, Any]:
    """Dated living-cost checklist for the same 30-day Today plan."""
    categories = list(session.exec(select(BudgetCategory)).all())
    linked = list(session.exec(select(Transaction).where(
        or_(Transaction.planned_budget_category_id.is_not(None), func.lower(Transaction.category) == "discretionary"),
    )).all())
    trips = []
    everyday = []
    for category in categories:
        if _is_weekly_groceries(category):
            planned = as_of - timedelta(days=(as_of.weekday() - category.planned_weekday) % 7)
            while planned <= as_of + timedelta(days=30):
                recorded = [row for row in linked if row.planned_budget_category_id == category.id
                            and row.planned_spending_date == planned]
                trips.append({
                    "category_id": category.id, "name": category.name,
                    "planned_date": planned.isoformat(),
                    "planned_amount": round(float(category.usual_amount or 0), 2),
                    "recorded_amount": round(sum(row.amount for row in recorded), 2),
                    "recorded": bool(recorded),
                    "awaiting_checking_update": False,
                })
                planned += timedelta(days=7)
        elif category.period == "day" and category.usual_amount is not None:
            for offset in range(31):
                planned = as_of + timedelta(days=offset)
                recorded = [row for row in linked if row.planned_budget_category_id == category.id
                            and row.planned_spending_date == planned]
                everyday.append({
                    "category_id": category.id, "name": category.name,
                    "planned_date": planned.isoformat(),
                    "planned_amount": round(float(category.usual_amount), 2),
                    "recorded_amount": round(sum(row.amount for row in recorded), 2),
                    "awaiting_checking_update": False,
                })
    return {
        "grocery_trips": trips,
        "everyday": everyday,
        "pending_checking_count": 0,
    }


def planned_spending_daily_events(
    session: Session, start: date, end: date
) -> list[dict[str, Any]]:
    """Spread entered living allowances over actual dates for cash-gap timing.

    This is a planning estimate, not a claim about when the owner will shop.
    The current month's already-recorded outflows reduce its unspent allowance.
    """
    if end < start:
        return []
    categories = list(session.exec(select(BudgetCategory)).all())
    if not categories:
        return []
    groceries = [row for row in categories if _is_weekly_groceries(row)]
    other_categories = [row for row in categories if not _is_weekly_groceries(row)]
    daily_category_ids = {row.id for row in other_categories if row.period == "day"}
    linked = list(session.exec(select(Transaction).where(
        Transaction.planned_budget_category_id.is_not(None),
        Transaction.planned_spending_date >= start,
        Transaction.planned_spending_date <= end,
    )).all())
    linked_by_day: dict[date, list[Transaction]] = {}
    linked_by_trip: dict[tuple[str, date], list[Transaction]] = {}
    for transaction in linked:
        if transaction.planned_spending_date is None:
            continue
        linked_by_day.setdefault(transaction.planned_spending_date, []).append(transaction)
        linked_by_trip.setdefault(
            (str(transaction.planned_budget_category_id), transaction.planned_spending_date), []
        ).append(transaction)
    spent = {category.name.casefold(): 0.0 for category in other_categories}
    for transaction in session.exec(select(Transaction).where(
        Transaction.transaction_date >= start.replace(day=1),
        Transaction.transaction_date <= start,
        Transaction.amount > 0,
    )).all():
        key = transaction.category.casefold()
        if key in spent:
            spent[key] += float(transaction.amount)
    day = start
    events: list[dict[str, Any]] = []
    while day <= end:
        month_last = date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])
        segment_end = min(month_last, end)
        days_left = (month_last - day).days + 1
        amount_left = sum(
            max(0.0, float(row.usual_amount or 0)) * days_left /
            (1 if row.period == "day" else 7)
            if row.usual_amount is not None and row.period in {"day", "week"}
            else max(0.0, budget_for_month(row, day.year, day.month)
                     - (spent[row.name.casefold()] if day == start else 0.0))
            for row in other_categories
        )
        amount_cents = int(round(amount_left * 100))
        daily_cents, remainder_cents = divmod(amount_cents, days_left)
        date_cursor = day
        day_index = 0
        while date_cursor <= segment_end:
            scheduled = (daily_cents + (1 if day_index < remainder_cents else 0)) / 100
            daily_actual = [row for row in linked_by_day.get(date_cursor, [])
                            if row.planned_budget_category_id in daily_category_ids]
            remaining = max(0.0, scheduled - sum(row.amount for row in daily_actual))
            events.append({
                "date": date_cursor.isoformat(), "kind": "obligation",
                "amount": round(remaining, 2),
                "name": "Everyday spending", "spending_kind": "daily",
                "category": "living",
                "awaiting_checking_update": False,
                "pending_checking_amount": 0.0,
            })
            date_cursor += timedelta(days=1)
            day_index += 1
        day = segment_end + timedelta(days=1)
    # A recorded purchase is paid, including legacy rows whose historical
    # reconciliation flag is false. Checking is entered separately by the owner.
    for category in groceries:
        target = int(category.planned_weekday if category.planned_weekday is not None else 5)
        trip = start + timedelta(days=(target - start.weekday()) % 7)
        while trip <= end:
            recorded = linked_by_trip.get((category.id, trip), [])
            amount = 0.0 if recorded else float(category.usual_amount or 0)
            if amount <= 0:
                trip += timedelta(days=7)
                continue
            events.append({
                "date": trip.isoformat(), "kind": "obligation",
                "amount": round(amount, 2),
                "name": category.name, "category": "living",
                "spending_kind": "groceries", "suggested_day": True,
                "awaiting_checking_update": False,
                "pending_checking_amount": 0.0,
            })
            trip += timedelta(days=7)
    # A stale manual balance must not release yesterday's assumed essentials
    # just because the forecast rolls forward. Recorded purchases are already
    # paid; reserve only the unrecorded remainder of elapsed habits.
    profile = session.exec(select(BorrowerProfile)).first()
    if profile and profile.pay_period_allowance_enabled and profile.checking_confirmed_at:
        confirmed_day = profile.checking_confirmed_at.date()
        if confirmed_day < start:
            actuals = list(session.exec(select(Transaction).where(
                Transaction.planned_spending_date >= confirmed_day,
                Transaction.planned_spending_date < start,
            )).all())
            daily_estimate = 0.0
            grocery_estimate = 0.0
            elapsed = confirmed_day
            while elapsed < start:
                for category in categories:
                    matches = [row for row in actuals if row.planned_budget_category_id == category.id
                               and row.planned_spending_date == elapsed]
                    actual = sum(max(0, row.amount) for row in matches)
                    if category.period == "day" and category.usual_amount is not None:
                        daily_estimate += max(0, float(category.usual_amount) - actual)
                    elif _is_weekly_groceries(category) and elapsed.weekday() == category.planned_weekday:
                        grocery_estimate += max(0, float(category.usual_amount or 0) - actual)
                elapsed += timedelta(days=1)
            for amount, label, kind in ((daily_estimate, "Assumed daily spending since checking update", "daily"),
                                        (grocery_estimate, "Assumed groceries since checking update", "groceries")):
                if amount > 0:
                    events.append({"date": start.isoformat(), "kind": "obligation", "amount": round(amount, 2),
                                   "name": label, "spending_kind": kind, "category": "living"})
    events.sort(key=lambda row: (row["date"], row.get("spending_kind") != "daily"))
    return events


def sync_category_spending(session: Session) -> None:
    """Recalculate the current calendar month's positive outflows only."""
    categories = {c.id: c for c in session.exec(select(BudgetCategory)).all()}
    today = date.today()
    txs = session.exec(
        select(Transaction).where(
            Transaction.transaction_date >= today.replace(day=1),
            Transaction.transaction_date <= today,
            Transaction.amount > 0,
        )
    ).all()
    for cat in categories.values():
        cat.spent_this_period = 0.0
    name_map = {c.name.lower(): c for c in categories.values()}
    for tx in txs:
        key = tx.category.lower()
        if key in name_map:
            name_map[key].spent_this_period += tx.amount
    for cat in categories.values():
        session.add(cat)
    session.commit()
