"""Payday-to-payday cash flow engine — conservative safe-to-spend from cash on hand."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from app.config import settings
from app.core.account_balances import account_totals
from app.core.bill_schedule import bill_events_in_range, sum_bills_due_in_period
from app.core.budget_engine import planned_spending_in_window
from app.core.credit_card_schedule import (
    cards_for_cashflow,
    monthly_credit_card_minimums_total,
    sum_cards_due_in_period,
)
from app.core.debt_schedule import (
    debt_events_in_range,
    debts_for_cashflow,
    monthly_debt_minimums_total,
    sum_debts_due_in_period,
)
from app.core.housing_schedule import (
    housing_events_in_range,
    monthly_housing_payments_total,
    sum_housing_due_in_period,
)
from app.core.misc_schedule import misc_events_in_range, sum_misc_due_in_period
from app.core.monthly_obligations import monthly_obligations_breakdown
from app.db.models import (
    Bill,
    BorrowerProfile,
    CreditCard,
    Debt,
    IncomeSource,
    LiquidAccount,
    MiscSpending,
    MortgageAccount,
)


def _advance_income_date(d: date, freq: str | None) -> date:
    from app.core.bill_schedule import advance_anchor

    f = (freq or "biweekly").lower()
    if f == "weekly":
        return d + timedelta(days=7)
    if f == "monthly":
        return advance_anchor(d, "monthly")
    if f == "biweekly":
        return d + timedelta(days=14)
    return advance_anchor(d, f)


def _rewind_income_date(d: date, freq: str | None) -> date:

    f = (freq or "biweekly").lower()
    if f == "weekly":
        return d - timedelta(days=7)
    if f == "monthly":
        month = d.month - 1 or 12
        year = d.year - (1 if d.month == 1 else 0)
        day = min(d.day, 28)
        return date(year, month, day)
    if f == "biweekly":
        return d - timedelta(days=14)
    return d - timedelta(days=14)


def _next_pay_date(sources: list[IncomeSource], today: date) -> tuple[date | None, float]:
    """Next paycheck date and income expected on that payday (not summed across unrelated schedules)."""
    if not sources:
        return None, 0.0
    best: date | None = None
    for src in sources:
        if src.next_pay_date:
            d = src.next_pay_date
            if d >= today and (best is None or d < best):
                best = d
    if best is None:
        best = today + timedelta(days=14)
    expected = sum(
        float(s.amount)
        for s in sources
        if s.next_pay_date and s.next_pay_date == best
    )
    if expected <= 0:
        expected = sum(float(s.amount) for s in sources[:1])
    return best, expected


def _following_pay_date(sources: list[IncomeSource], after_pay: date) -> date:
    """Earliest paycheck strictly after after_pay across all income sources."""
    candidates: list[date] = []
    for src in sources:
        if not src.next_pay_date:
            continue
        d = src.next_pay_date
        freq = src.pay_frequency or "biweekly"
        while d <= after_pay:
            d = _advance_income_date(d, freq)
        candidates.append(d)
    if not candidates:
        return after_pay + timedelta(days=14)
    return min(candidates)


def _income_in_period(sources: list[IncomeSource], period_start: date, period_end: date) -> float:
    """Sum paycheck amounts with pay dates in [period_start, period_end]."""
    total = 0.0
    for src in sources:
        if not src.next_pay_date:
            continue
        freq = src.pay_frequency or "biweekly"
        d = src.next_pay_date
        while d > period_end:
            d = _rewind_income_date(d, freq)
        while d < period_start:
            d = _advance_income_date(d, freq)
        while d <= period_end:
            if d >= period_start:
                total += float(src.amount)
            d = _advance_income_date(d, freq)
    return round(total, 2)


def _resolve_checking_balance(session: Session, override: float | None) -> float:
    if override is not None:
        return float(override)
    totals = account_totals(session)
    recorded = session.exec(select(BorrowerProfile)).first() is not None
    recorded = recorded or session.exec(select(LiquidAccount).where(
        LiquidAccount.include_in_cashflow == True,  # noqa: E712
        LiquidAccount.account_type != "savings",
    )).first() is not None
    if recorded or totals["checking"] != 0:
        return totals["checking"]
    return float(settings.starting_cash)


def effective_buffer_from_borrower(borrower: dict[str, Any] | None) -> float:
    """Protect actual earmarked cash when known; retain target-only legacy protection."""
    if borrower is not None:
        if borrower.get("cash_cushion_balance") is not None:
            return max(0.0, float(borrower["cash_cushion_balance"]))
        return max(0.0, float(borrower.get("target_buffer") or 0))
    return max(0.0, float(settings.minimum_buffer))


def _effective_buffer(session: Session) -> float:
    """Holdback subtracted from safe-to-spend (session-backed)."""
    profile = session.exec(select(BorrowerProfile)).first()
    if profile is not None:
        if profile.cash_cushion_balance is not None:
            return max(0.0, float(profile.cash_cushion_balance))
        return max(0.0, float(profile.target_buffer or 0))
    return max(0.0, float(settings.minimum_buffer))


def safe_to_spend_detail(amount: float, buffer: float) -> str:
    """User-facing clause for safe-to-spend (omits buffer when none configured)."""
    base = (
        f"About ${amount:,.0f} you can spend today while scheduled bills and "
        "planned paychecks still clear"
    )
    if buffer > 0:
        return f"{base} (keeping ${buffer:,.0f} buffer)."
    return f"{base}."


def _sum_obligations_in_period(session: Session, period_start: date, period_end: date) -> float:
    return round(
        sum_bills_due_in_period(session, period_start, period_end)
        + sum_debts_due_in_period(session, period_start, period_end)
        + sum_housing_due_in_period(session, period_start, period_end)
        + sum_cards_due_in_period(session, period_start, period_end)
        + sum_misc_due_in_period(session, period_start, period_end),
        2,
    )


def _missing_obligation_due_date_warnings(session: Session) -> list[str]:
    """Actionable hints when scheduled obligations lack due dates (not “all bills after payday”)."""
    warnings: list[str] = []
    for bill in session.exec(select(Bill)).all():
        if bill.required and not bill.due_date:
            warnings.append(f'Add a due date for bill "{bill.name}" under My Money')
    for debt in session.exec(select(Debt)).all():
        if float(debt.minimum_payment or 0) > 0 and not debt.due_date:
            warnings.append(f'Add a due date for debt "{debt.creditor}" under My Money')
    for mortgage in session.exec(select(MortgageAccount)).all():
        pay = float(mortgage.monthly_payment or 0) + float(mortgage.escrow_amount or 0)
        if pay > 0 and not mortgage.due_by_date:
            warnings.append(f'Add a due date for mortgage "{mortgage.lender}" under My Money')
    for card in session.exec(select(CreditCard)).all():
        if float(card.minimum_payment or 0) > 0 and not card.due_date:
            name = card.card_name or card.issuer or "Credit card"
            warnings.append(f'Add a due date for {name} under My Money')
    profile = session.exec(select(BorrowerProfile)).first()
    if profile and float(profile.monthly_rent or 0) > 0 and not profile.rent_due_date:
        warnings.append("Add a rent due date under My Money → Cash")
    return warnings


def _obligations_until_pay(
    session: Session,
    period_start: date,
    period_end: date,
) -> tuple[float, list[dict[str, Any]]]:
    """Bills, debt minimums, and mortgage payments due on or before next payday."""
    bills = list(session.exec(select(Bill)).all())
    debts = debts_for_cashflow(session)
    cards = cards_for_cashflow(session)

    events: list[dict[str, Any]] = []
    events.extend(bill_events_in_range(bills, period_start, period_end, session))
    events.extend(debt_events_in_range(debts, period_start, period_end, session))
    events.extend(housing_events_in_range(session, period_start, period_end))
    from app.core.credit_card_payment import credit_card_events_in_range_unpaid
    from app.core.credit_card_payment import paid_keys as card_paid_keys

    events.extend(credit_card_events_in_range_unpaid(
        cards, period_start, period_end, card_paid_keys(session), session
    ))
    misc = list(session.exec(select(MiscSpending)).all())
    events.extend(misc_events_in_range(misc, period_start, period_end))
    events.sort(key=lambda e: (e["date"], e.get("name", "")))

    total = round(sum(float(e["amount"]) for e in events), 2)
    return total, events


def _build_headline(cf: dict[str, Any]) -> str:
    cash = float(cf["checking_balance"])
    reserved = float(cf.get("reserved_for_pay_cycle", cf["committed_until_pay"]))
    safe = float(cf["safe_to_spend"])
    following = cf.get("following_pay_date") or cf.get("next_pay_date") or "your next pay cycle"

    if reserved > cash:
        short = reserved - cash
        return (
            f"${reserved:,.0f} must stay in checking through {following}, but you only have "
            f"${cash:,.0f}. Do not spend — you are ${short:,.0f} short before the next pay cycle ends."
        )
    if safe <= 0:
        return (
            f"After ${reserved:,.0f} reserved for bills through {following}, "
            f"nothing is left for discretionary spending from today's ${cash:,.0f} balance."
        )
    return (
        f"Up to ${safe:,.0f} discretionary from ${cash:,.0f} in checking after "
        f"${reserved:,.0f} set aside for obligations through {following}."
    )


def calculate_cashflow(session: Session, starting_cash: float | None = None) -> dict[str, Any]:
    """
    Conservative cashflow snapshot.

    safe_to_spend is money you can spend TODAY from checking only, after obligations
    through the current pay cycle (including bills due after the next paycheck that
    paycheck must cover) minus expected income in that window, and your buffer.
    """
    today = date.today()
    cash = _resolve_checking_balance(session, starting_cash)
    buffer = _effective_buffer(session)
    sources = list(session.exec(select(IncomeSource)).all())
    next_pay, expected_income = _next_pay_date(sources, today)
    following_pay = _following_pay_date(sources, next_pay) if next_pay else today + timedelta(days=28)
    pay_cycle_end = following_pay - timedelta(days=1)

    period_end = next_pay or (today + timedelta(days=14))
    period_start = today

    required_bills = sum_bills_due_in_period(session, period_start, period_end)
    debt_due_period = sum_debts_due_in_period(session, period_start, period_end)
    mortgage_due_period = sum_housing_due_in_period(session, period_start, period_end)
    card_due_period = sum_cards_due_in_period(session, period_start, period_end)
    misc_due_period = sum_misc_due_in_period(session, period_start, period_end)
    _, upcoming_payments = _obligations_until_pay(session, period_start, period_end)
    committed_until_pay = round(
        required_bills
        + debt_due_period
        + mortgage_due_period
        + card_due_period
        + misc_due_period,
        2,
    )

    cycle_obligations = _sum_obligations_in_period(session, period_start, pay_cycle_end)
    from app.core.income_payment import sum_received_in_range, sum_unreceived_scheduled_in_range

    income_in_cycle = round(
        sum_received_in_range(session, period_start, pay_cycle_end)
        + sum_unreceived_scheduled_in_range(session, period_start, pay_cycle_end),
        2,
    )
    income_already_received = sum_received_in_range(session, period_start, today)
    reserved_for_pay_cycle = round(max(0.0, cycle_obligations - income_already_received), 2)

    obligations = monthly_obligations_breakdown(session)
    planned_variable = planned_spending_in_window(session, today, pay_cycle_end)

    cash_after_reserved = cash - reserved_for_pay_cycle
    safe_to_spend_now = max(0.0, cash_after_reserved - buffer)

    projected_end = cash + income_in_cycle - cycle_obligations - planned_variable
    safe_after_paycheck = max(0.0, projected_end - buffer)

    warnings: list[str] = []
    if cash <= 0 and not sources:
        warnings.append("Enter your checking balance under My Money → Cash")
    elif cash <= 0:
        warnings.append("Checking balance is zero — safe to spend is $0")
    if reserved_for_pay_cycle > cash:
        warnings.append(
            f"Obligations through {pay_cycle_end.isoformat()} need ${reserved_for_pay_cycle:,.0f} "
            f"from checking (${cycle_obligations:,.0f} due, ${income_already_received:,.0f} already received "
            f"of ${income_in_cycle:,.0f} expected this cycle) "
            f"but balance is ${cash:,.0f}"
        )
    for hint in _missing_obligation_due_date_warnings(session):
        warnings.append(hint)
    if cycle_obligations > income_in_cycle and pay_cycle_end > (next_pay or today):
        post_pay_due = round(
            _sum_obligations_in_period(
                session,
                (next_pay or today) + timedelta(days=1),
                pay_cycle_end,
            ),
            2,
        )
        if post_pay_due > expected_income:
            warnings.append(
                f"${post_pay_due:,.0f} is due after your {period_end.isoformat()} paycheck "
                f"(${expected_income:,.0f}) — holding cash until that income arrives"
            )

    if cash_after_reserved < 0:
        risk = "danger"
    elif safe_to_spend_now <= 0:
        risk = "danger" if reserved_for_pay_cycle > cash else "tight"
    elif projected_end < 0:
        risk = "tight"
        warnings.append("Even after your next paycheck, the pay period may finish tight")
    elif projected_end < buffer:
        risk = "tight"
    else:
        risk = "safe"

    result = {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "pay_cycle_end": pay_cycle_end.isoformat(),
        "following_pay_date": following_pay.isoformat(),
        "checking_balance": round(cash, 2),
        "starting_cash": round(cash, 2),
        "expected_income": round(expected_income, 2),
        "income_in_pay_cycle": income_in_cycle,
        "income_already_received": income_already_received,
        "cycle_obligations": cycle_obligations,
        "committed_until_pay": committed_until_pay,
        "reserved_for_pay_cycle": reserved_for_pay_cycle,
        "required_bills": round(required_bills, 2),
        "debt_due_this_period": round(debt_due_period, 2),
        "credit_card_due_this_period": round(card_due_period, 2),
        "mortgage_due_this_period": round(mortgage_due_period, 2),
        "travel_due_this_period": 0.0,
        "misc_due_this_period": round(misc_due_period, 2),
        "monthly_bills_estimate": obligations["bills"],
        "monthly_travel": 0.0,
        "monthly_misc": obligations.get("misc", 0),
        "monthly_debt_minimums": monthly_debt_minimums_total(session),
        "monthly_credit_card_minimums": monthly_credit_card_minimums_total(session),
        "monthly_mortgage_payments": monthly_housing_payments_total(session),
        "monthly_obligations_total": obligations["total"],
        "monthly_obligations": obligations,
        "debt_minimums": round(debt_due_period, 2),
        "planned_variable_spending": round(planned_variable, 2),
        "minimum_buffer": round(buffer, 2),
        "safe_to_spend": round(safe_to_spend_now, 2),
        "safe_to_spend_after_paycheck": round(safe_after_paycheck, 2),
        "projected_end_balance": round(projected_end, 2),
        "risk_level": risk,
        "warnings": warnings,
        "next_pay_date": period_end.isoformat() if period_end else None,
        "upcoming_payments": upcoming_payments[:20],
        "headline": _build_headline(
            {
                "checking_balance": cash,
                "committed_until_pay": committed_until_pay,
                "reserved_for_pay_cycle": reserved_for_pay_cycle,
                "safe_to_spend": safe_to_spend_now,
                "next_pay_date": period_end.isoformat() if period_end else None,
                "following_pay_date": following_pay.isoformat(),
            }
        ),
    }
    from app.core.liquidity_planner import build_liquidity_plan, merge_liquidity_into_cashflow

    plan = build_liquidity_plan(session, starting_cash)
    return merge_liquidity_into_cashflow(result, plan)
