"""Savings goal pacing — safe contribution recommendations without impacting obligations."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlmodel import Session, select

from app.core.cashflow_engine import calculate_cashflow
from app.db.models import SavingsGoal


def can_contribute_to_goals(cashflow: dict[str, Any]) -> bool:
    """Optional goals draw only from V2 deployable capital, never protected cash."""
    state = cashflow.get("money_state") or {}
    if state:
        return float(state.get("deployable_surplus") or 0) > 0
    # Compatibility with historical snapshots captured before the V2 contract.
    return (
        cashflow.get("risk_level") == "safe"
        and float(cashflow.get("safe_to_spend") or 0) > 0
        and float(cashflow.get("projected_end_balance") or 0)
        >= float(cashflow.get("minimum_buffer") or 0)
    )


def _months_until(due: date, today: date) -> float:
    days = (due - today).days
    if days <= 0:
        return 0.0
    return max(days / 30.44, 1 / 30.44)


def _block_reason(cashflow: dict[str, Any]) -> str:
    state = cashflow.get("money_state") or {}
    if state and float(state.get("deployable_surplus") or 0) <= 0:
        return (
            "Protected cash, expected spending, uncertainty protection, or the forward "
            "timeline leaves no deployable surplus for optional goals right now."
        )
    risk = cashflow.get("risk_level", "unknown")
    if risk == "danger":
        return (
            "Bills and obligations take priority — cashflow is tight. "
            "Goal contributions are paused until your finances stabilize."
        )
    if risk == "tight":
        return (
            "Cashflow is tight after bills and debt minimums. "
            "Focus on required payments before saving toward goals."
        )
    if float(cashflow.get("safe_to_spend") or 0) <= 0:
        return "No flexible surplus after bills, debt minimums, and buffer — goals are paused."
    return "Not enough surplus to recommend goal contributions right now."


def _urgency_score(goal: SavingsGoal, today: date) -> float:
    remaining = max(0.0, float(goal.target_amount) - float(goal.saved_amount))
    if remaining <= 0:
        return -1.0
    if goal.due_date is None:
        return 0.5
    months = _months_until(goal.due_date, today)
    if months <= 0:
        return 1000.0 + remaining
    return remaining / months


def analyze_goal(goal: SavingsGoal, cashflow: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    ref = today or date.today()
    remaining = round(max(0.0, float(goal.target_amount) - float(goal.saved_amount)), 2)
    progress_pct = (
        round(min(100.0, (float(goal.saved_amount) / float(goal.target_amount)) * 100), 1)
        if float(goal.target_amount) > 0
        else 0.0
    )

    base: dict[str, Any] = {
        "goal_id": goal.id,
        "name": goal.name,
        "target_amount": float(goal.target_amount),
        "saved_amount": float(goal.saved_amount),
        "remaining": remaining,
        "progress_pct": progress_pct,
        "due_date": goal.due_date.isoformat() if goal.due_date else None,
        "enabled": goal.enabled,
        "linked_account_id": goal.linked_account_id,
        "notes": goal.notes,
    }

    if remaining <= 0:
        return {
            **base,
            "status": "complete",
            "required_monthly": 0.0,
            "recommended_contribution": 0.0,
            "on_track": True,
            "message": f"{goal.name} target reached.",
        }

    required_monthly: float | None = None
    if goal.due_date:
        months_left = _months_until(goal.due_date, ref)
        if months_left <= 0:
            required_monthly = remaining
        else:
            required_monthly = round(remaining / months_left, 2)

    if not goal.enabled:
        return {
            **base,
            "status": "disabled",
            "required_monthly": required_monthly,
            "recommended_contribution": 0.0,
            "on_track": None,
            "message": "Goal is off — enable it to include in recommendations.",
        }

    if not can_contribute_to_goals(cashflow):
        msg = _block_reason(cashflow)
        if required_monthly and required_monthly > 0:
            msg += f" You would need about ${required_monthly:,.0f}/mo to stay on pace."
        return {
            **base,
            "status": "blocked",
            "required_monthly": required_monthly,
            "recommended_contribution": 0.0,
            "on_track": False if required_monthly else None,
            "blocked_reason": msg,
            "message": msg,
        }

    return {
        **base,
        "status": "ready",
        "required_monthly": required_monthly,
        "recommended_contribution": 0.0,
        "on_track": True,
        "message": "",
    }


def _allocate_surplus(
    ready: list[dict[str, Any]],
    surplus: float,
) -> list[dict[str, Any]]:
    if surplus <= 0 or not ready:
        return ready

    pool = surplus
    out: list[dict[str, Any]] = []
    for item in sorted(ready, key=lambda g: -float(g.get("_urgency") or 0)):
        remaining = float(item["remaining"])
        required = item.get("required_monthly")
        if required is not None and float(required) > 0:
            want = min(float(required), remaining, pool)
        else:
            want = min(pool * 0.25, remaining) if pool > 50 else 0.0
        rec = round(max(0.0, want), 2)
        pool = round(max(0.0, pool - rec), 2)

        on_track = True
        msg_parts: list[str] = []
        if rec > 0:
            msg_parts.append(f"Safe to put ${rec:,.0f} toward {item['name']} now.")
        if required is not None and float(required) > 0:
            if rec < float(required) * 0.9:
                on_track = False
                msg_parts.append(
                    f"Need ${float(required):,.0f}/mo to hit your deadline — "
                    "current surplus cannot fully cover the pace."
                )
            elif rec >= float(required) * 0.9:
                msg_parts.append(f"On pace for your {item.get('due_date') or 'target'} deadline.")
        elif rec > 0:
            msg_parts.append("No deadline — optional contribution when cashflow allows.")

        updated = {k: v for k, v in item.items() if not k.startswith("_")}
        updated["recommended_contribution"] = rec
        updated["on_track"] = on_track
        updated["message"] = " ".join(msg_parts) if msg_parts else updated.get("message", "")
        out.append(updated)

    return out


def build_goal_plan(session: Session, cashflow: dict[str, Any] | None = None) -> dict[str, Any]:
    goals = list(session.exec(select(SavingsGoal).order_by(SavingsGoal.created_at.asc())).all())
    cf = cashflow or calculate_cashflow(session)
    today = date.today()
    state = cf.get("money_state") or {}
    surplus = (
        float(state.get("deployable_surplus") or 0)
        if state
        else float(cf.get("safe_to_spend") or 0)
    ) if can_contribute_to_goals(cf) else 0.0

    analyses: list[dict[str, Any]] = []
    ready: list[dict[str, Any]] = []
    for goal in goals:
        row = analyze_goal(goal, cf, today)
        if row["status"] == "ready":
            row["_urgency"] = _urgency_score(goal, today)
            ready.append(row)
        else:
            analyses.append(row)

    allocated = _allocate_surplus(ready, surplus)
    analyses.extend(allocated)
    analyses.sort(key=lambda g: (not g.get("enabled"), g.get("name", "")))

    enabled_count = sum(1 for g in goals if g.enabled)
    total_recommended = round(sum(float(g.get("recommended_contribution") or 0) for g in analyses), 2)

    summary = "No savings goals yet."
    if goals and enabled_count == 0:
        summary = f"{len(goals)} goal(s) — all disabled; enable to include in planning."
    elif enabled_count > 0 and not can_contribute_to_goals(cf):
        summary = _block_reason(cf)
    elif total_recommended > 0:
        summary = f"Safe to allocate ${total_recommended:,.0f} across enabled goals this period."
    elif enabled_count > 0:
        summary = "Goals are enabled but no surplus is available for contributions right now."

    return {
        "goals": analyses,
        "can_contribute": can_contribute_to_goals(cf),
        "available_surplus": round(surplus, 2),
        "total_recommended": total_recommended,
        "enabled_count": enabled_count,
        "summary": summary,
        "cashflow_risk_level": cf.get("risk_level"),
        "capital_contract": "v2_deployable_surplus" if state else "legacy_safe_to_spend",
    }


def goal_recommendations_for_snapshot(
    cashflow: dict[str, Any],
    goal_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build recommendation dicts for the recommendation engine."""
    if goal_plan.get("enabled_count", 0) == 0:
        return []

    recs: list[dict[str, Any]] = []
    if not goal_plan.get("can_contribute"):
        behind = [
            g
            for g in goal_plan.get("goals") or []
            if g.get("enabled") and g.get("status") == "blocked" and g.get("required_monthly")
        ]
        if behind:
            g = behind[0]
            recs.append({
                "title": f"Goals paused — {g['name']} needs attention",
                "action": g.get("blocked_reason") or goal_plan.get("summary", ""),
                "action_type": "avoid_action",
                "category": "savings",
                "priority": 3,
                "why": g.get("message") or goal_plan.get("summary", ""),
                "math": {"goal_id": g["goal_id"], "required_monthly": g.get("required_monthly")},
            })
        return recs

    for g in goal_plan.get("goals") or []:
        if g.get("status") != "ready" or float(g.get("recommended_contribution") or 0) <= 0:
            continue
        amt = float(g["recommended_contribution"])
        recs.append({
            "title": f"Save ${amt:,.0f} toward {g['name']}",
            "action": g.get("message") or f"Transfer ${amt:,.0f} when convenient — bills and debts are covered.",
            "action_type": "fund_savings_goal",
            "category": "savings",
            "priority": 5,
            "why": g.get("message") or "Cashflow is safe; this keeps you on pace without impacting obligations.",
            "math": {
                "goal_id": g["goal_id"],
                "recommended_contribution": amt,
                "required_monthly": g.get("required_monthly"),
                "remaining": g.get("remaining"),
                "due_date": g.get("due_date"),
            },
        })
    return recs


__all__ = [
    "analyze_goal",
    "build_goal_plan",
    "can_contribute_to_goals",
    "goal_recommendations_for_snapshot",
]
