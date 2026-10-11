"""Deterministic fortnight allowance with monthly emergency savings and runway.

Amounts are integer cents. Every dated prefix funds essentials, emergency growth,
all allowance releases so far, and one additional fortnight of breathing room.
This is a recorded-data plan, not an authorization to move money.
"""
from datetime import date, timedelta
from decimal import ROUND_FLOOR, Decimal

from sqlmodel import Session, select

from app.core.account_balances import account_totals
from app.core.bill_schedule import _advance_months
from app.db.models import BorrowerProfile, CashCushionContribution, IncomeSource, PaymentArchiveEvent, Transaction

ALLOWANCE_RELEASE_PERCENT = 90


def cents(value: float) -> int:
    return int((Decimal(str(value)) * 100).quantize(Decimal('1')))


def build_pay_period_allowance(session: Session, *, as_of: date, through: date,
                               opening_cash: float, payment_rows: list[dict]) -> dict | None:
    profile = session.exec(select(BorrowerProfile)).first()
    if not profile or not profile.pay_period_allowance_enabled:
        return None
    sources = list(session.exec(select(IncomeSource)).all())
    anchors = [row for row in sources if row.next_pay_date and row.pay_frequency == 'biweekly']
    primary = max(anchors, key=lambda row: (row.amount, row.id)) if anchors else None
    anchor = primary.next_pay_date if primary else date(2026, 1, 2)
    period_start = anchor + timedelta(days=((as_of - anchor).days // 14) * 14)
    period_end = period_start + timedelta(days=13)
    goal = max(0, cents(profile.target_buffer))
    current = None if profile.cash_cushion_balance is None else max(0, cents(profile.cash_cushion_balance))
    monthly = max(0, cents(profile.cushion_monthly_contribution))
    # Savings are outside the checking timeline. Protect only the part of the
    # owner's existing earmark still held within checking, never savings twice.
    outside_checking = min(current or 0, max(0, cents(account_totals(session)['savings'])))
    reserve = max(0, (current or 0) - outside_checking)
    transactions = list(session.exec(select(Transaction).where(
        Transaction.transaction_date <= as_of,
    )).all())
    optional = [row for row in transactions if row.category.strip().casefold() == 'discretionary'
                and row.amount > 0]
    spent = sum(cents(row.amount) for row in optional if row.transaction_date >= period_start)
    daily_changes: dict[date, int] = {}
    for row in payment_rows:
        day = date.fromisoformat(row['date'])
        amount = cents(row['amount']) * (1 if row['kind'] == 'income' else -1)
        daily_changes[day] = daily_changes.get(day, 0) + amount
    cash = cents(opening_cash)
    balances = {}
    day = as_of
    while day <= through:
        cash += daily_changes.get(day, 0)
        balances[day] = cash
        day += timedelta(days=1)
    contributions = list(session.exec(select(CashCushionContribution)).all())
    schedule = []
    month = as_of.replace(day=1)
    projected_fund = current or 0
    while month <= through:
        following = _advance_months(month, 1)
        month_end = min(through, following - timedelta(days=1))
        already = sum(cents(row.amount) for row in contributions
                      if month <= row.contribution_date < following)
        addition = min(max(0, monthly - already), max(0, goal - projected_fund))
        if addition:
            incomes = sorted({date.fromisoformat(row['date']) for row in payment_rows
                              if row['kind'] == 'income'
                              and max(as_of, month) <= date.fromisoformat(row['date']) <= month_end})
            candidates = sorted({max(as_of, month), *incomes})
            # Prefer the earliest paycheck that can fund this month's saving
            # without squeezing essentials later in the same month.
            planned = next((candidate for candidate in candidates
                            if min(balances[d] for d in balances if candidate <= d <= month_end)
                            >= reserve + addition), candidates[-1])
            reserve += addition
            projected_fund += addition
            schedule.append({'date': planned.isoformat(), 'amount': addition / 100,
                             'projected_fund': projected_fund / 100})
        month = following
    releases = [as_of]
    release = period_end + timedelta(days=1)
    while release <= through:
        releases.append(release)
        release += timedelta(days=14)
    held = max(0, (current or 0) - outside_checking)
    funded_gap = 0
    constraints = []
    by_date = {date.fromisoformat(row['date']): cents(row['amount']) for row in schedule}
    for day, balance in balances.items():
        held += by_date.get(day, 0)
        funded_gap = max(funded_gap, held - balance)
        # Current allowance remainder + future full releases + one full
        # allowance of runway. Past optional spending consumes this period.
        count = sum(release <= day for release in releases)
        available = balance - held
        limit = (Decimal(available + spent) / Decimal(count + 1) if available >= count * spent
                 else Decimal(available) / Decimal(count))
        constraints.append((limit, day))
    # The current allowance can be spent immediately, before today's income.
    initial_held = max(0, (current or 0) - outside_checking)
    initial_available = cents(opening_cash) - initial_held
    initial_limit = (Decimal(initial_available + spent) / 2 if initial_available >= spent
                     else Decimal(initial_available))
    constraints.append((initial_limit, as_of))
    maximum, constraint_day = min(constraints)
    safe_budget = max(0, int(maximum.to_integral_value(rounding=ROUND_FLOOR)))
    unconfirmed_activity = bool(profile.checking_confirmed_at and session.exec(select(PaymentArchiveEvent).where(
        PaymentArchiveEvent.created_at > profile.checking_confirmed_at,
        PaymentArchiveEvent.action.in_(["marked_paid", "marked_received"]),
    )).first())
    known = current is not None and primary is not None and not unconfirmed_activity
    verification_reason = ("Update checking after the bill or paycheck you marked complete."
                           if unconfirmed_activity else
                           "Enter the emergency balance and a biweekly payday before relying on an allowance.")
    if not known or funded_gap > 0:
        safe_budget = 0
    # Release less than the already-safe allocation. The rest remains in the
    # existing discretionary cash and carries forward through recorded checking;
    # it is not another expense, emergency deposit, or invented saved balance.
    budget = safe_budget * ALLOWANCE_RELEASE_PERCENT // 100
    retained = safe_budget - budget
    current_retained = max(0, safe_budget - max(budget, spent))
    future_periods = max(0, len(releases) - 1)
    planned_retained = current_retained + future_periods * retained
    remaining = max(0, budget - spent)
    projection = []
    held = initial_held
    for day, balance in balances.items():
        held += by_date.get(day, 0)
        count = sum(release <= day for release in releases)
        future_spent = max(0, count - 1) * budget + remaining
        projection.append({'date': day.isoformat(), 'balanceAfterSpending': (balance - future_spent) / 100,
                           'cushion': (held + outside_checking) / 100, 'surprises': 0,
                           'uncommitted': 0, 'extraSpend': remaining / 100 if day <= period_end else budget / 100})
    return {'period_start': period_start.isoformat(), 'period_end': period_end.isoformat(),
            'next_reset': (period_end + timedelta(days=1)).isoformat(), 'period_days': 14,
            'budget': budget / 100, 'spent': spent / 100, 'remaining': remaining / 100,
            'comfort_holdback': safe_budget / 100, 'known': known, 'verification_reason': verification_reason,
            'total_discretionary_planned': (remaining + future_periods * budget + planned_retained) / 100,
            'safe_period_capacity': safe_budget / 100, 'retained_per_period': retained / 100,
            'planned_retained': planned_retained / 100, 'retention_percent': 100 - ALLOWANCE_RELEASE_PERCENT,
            'funding_shortfall': max(0, funded_gap) / 100,
            'emergency_current': None if current is None else current / 100,
            'emergency_goal': goal / 100, 'monthly_contribution': monthly / 100,
            'projected_emergency': projected_fund / 100,
            'contributions': schedule, 'constraint_date': constraint_day.isoformat(),
            'projection': projection, 'planning_through': through.isoformat()}
