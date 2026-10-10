"""Read-only V2 Today composition over established financial contracts."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from app.core.budget_engine import spending_attention
from app.core.cashflow_engine import calculate_cashflow
from app.core.household_advisor import build_advisory
from app.core.obligation_attention import build_obligation_attention
from app.core.opportunity_research import research_status
from app.core.v2_opportunities import top_moves
from app.db.models import BorrowerProfile, CashCushionContribution, FinancialAction, PaymentArchiveEvent


def _safety_status(cashflow: dict[str, Any]) -> str:
    """Never reassure on a bill-only forecast when current spending is constrained."""
    risk = str(cashflow.get("risk_level") or "tight")
    breakdown = (cashflow.get("money_state") or {}).get("today_breakdown") or {}
    balance = cashflow.get("checking_balance")
    lowest = breakdown.get("dated_lowest_after_normal_spending")
    if (risk == "danger" or (balance is not None and float(balance) <= 0)
            or (lowest is not None and float(lowest) < 0)
            or breakdown.get("coverage_status") == "shortfall"):
        return "at_risk"
    free = breakdown.get("total_flexible_30_days")
    if (breakdown.get("coverage_status") in {"unknown", "covered_below_cushion", "savings_shortfall"}
            or (free is not None and float(free) <= 0)):
        return "tight"
    if risk == "safe":
        return "safe"
    return "tight"


def _action_payload(action: FinancialAction) -> dict[str, Any]:
    return {column.name: getattr(action, column.name) for column in action.__table__.columns}


def build_today(session: Session) -> dict[str, Any]:
    """Return a bounded composition, never a second financial calculation."""
    cashflow = calculate_cashflow(session)
    money_state = dict(cashflow.get("money_state") or {})
    advisory = build_advisory(session, generate_hypotheses=False)
    actions = session.exec(
        select(FinancialAction).where(
            FinancialAction.opportunity_candidate_id.is_not(None),
            FinancialAction.status.in_(["open", "in_progress"]),
        )
    ).all()
    actions.sort(key=lambda row: (row.priority, str(row.due_date or "9999-12-31")))
    borrower = session.exec(select(BorrowerProfile)).first()
    unconfirmed_activity = 0
    if borrower and borrower.checking_confirmed_at:
        unconfirmed_activity = len(session.exec(select(PaymentArchiveEvent).where(
            PaymentArchiveEvent.created_at > borrower.checking_confirmed_at,
            PaymentArchiveEvent.action.in_(["marked_paid", "marked_received"]),
        )).all())
    allowance = money_state.get("allowance")
    recent_cushion = session.exec(select(CashCushionContribution).where(
        CashCushionContribution.contribution_date >= (
            date.today().replace(day=1) if allowance else date.today() - timedelta(days=29)
        )
    ).order_by(CashCushionContribution.created_at.desc())).first()
    cushion_step = float((money_state.get("today_breakdown") or {}).get("cash_cushion_refill") or 0)
    cushion_payload = {
        "amount": recent_cushion.amount if recent_cushion else round(cushion_step, 2),
        "date": (recent_cushion.contribution_date if recent_cushion else date.today()).isoformat(),
        "recorded": recent_cushion is not None,
    }
    if allowance:
        # A future monthly deposit is still an obligation to show in Due Soon.
        # Its amount/date come from the same schedule already reserved by the
        # allowance planner; never charge it again as a bill or expense.
        upcoming = next(iter(allowance["contributions"]), None)
        cushion_payload = {
            "amount": upcoming["amount"] if upcoming else 0,
            "date": upcoming["date"] if upcoming else date.today().isoformat(),
            "recorded": upcoming is None,
            "can_record": bool(upcoming and upcoming["date"] <= date.today().isoformat()
                               and allowance["known"] and allowance["funding_shortfall"] <= 0
                               and not unconfirmed_activity),
        }
    return {
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "safety_status": _safety_status(cashflow),
        "financial_state": money_state,
        "cashflow": {
            key: cashflow.get(key)
            for key in (
                "checking_balance",
                "next_pay_date",
                "expected_income",
                "upcoming_payments",
                "risk_level",
                "warnings",
                "horizons",
            )
        },
        "immediate_obligations": build_obligation_attention(session, window_days=30, max_items=200),
        "spending_attention": spending_attention(session, date.today()),
        "checking_balance_recorded": borrower.checking_balance if borrower else None,
        "checking_activity_pending": unconfirmed_activity,
        "cushion_step": cushion_payload,
        "top_moves": top_moves(session)[:3],
        "resilience": advisory["resilience"],
        "wealth_plan": advisory["wealth_plan"],
        "top_wealth_action": advisory["top_wealth_action"],
        "research": research_status(session),
        "active_candidate_actions": [_action_payload(row) for row in actions[:6]],
    }
