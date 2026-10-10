"""Savings goals API — optional targets with safe contribution planning."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.api.deps import get_db
from app.api.schemas import SavingsGoalCreate
from app.core.cashflow_engine import calculate_cashflow
from app.core.entry_validation import coerce_date_fields
from app.core.goal_planner import analyze_goal, build_goal_plan
from app.core.v2_opportunities import refresh_household_decision_dependents
from app.db.models import SavingsGoal
from app.events.bus import event_bus
from app.events.types import Event, EventType

router = APIRouter(prefix="/api/goals", tags=["goals"])


def _emit_cashflow(session: Session) -> None:
    cashflow = calculate_cashflow(session)
    refresh_household_decision_dependents(session, {"cashflow"}, cashflow=cashflow)
    event_bus.emit(
        Event(
            event_type=EventType.CASHFLOW_RECALCULATED.value,
            source="engine",
            payload=cashflow,
        )
    )


def _goal_payload(data: SavingsGoalCreate) -> dict:
    payload = data.model_dump()
    payload["name"] = str(payload.get("name") or "").strip()
    if not payload["name"]:
        raise HTTPException(400, "name is required")
    if float(payload.get("target_amount") or 0) <= 0:
        raise HTTPException(400, "target_amount must be positive")
    payload["saved_amount"] = max(0.0, float(payload.get("saved_amount") or 0))
    return payload


@router.get("")
def list_goals(session: Session = Depends(get_db)):
    goals = list(session.exec(select(SavingsGoal).order_by(SavingsGoal.created_at.asc())).all())
    plan = build_goal_plan(session)
    return {"goals": goals, "plan": plan}


@router.get("/plan")
def goal_plan(session: Session = Depends(get_db)):
    return build_goal_plan(session)


@router.post("")
def create_goal(data: SavingsGoalCreate, session: Session = Depends(get_db)):
    payload = coerce_date_fields(_goal_payload(data), "due_date")
    obj = SavingsGoal(**payload)
    session.add(obj)
    session.commit()
    session.refresh(obj)
    _emit_cashflow(session)
    cf = calculate_cashflow(session)
    return {"goal": obj, "analysis": analyze_goal(obj, cf)}


@router.put("/{goal_id}")
def update_goal(goal_id: str, data: SavingsGoalCreate, session: Session = Depends(get_db)):
    obj = session.get(SavingsGoal, goal_id)
    if not obj:
        raise HTTPException(404)
    payload = coerce_date_fields(_goal_payload(data), "due_date")
    for k, v in payload.items():
        setattr(obj, k, v)
    obj.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(obj)
    session.commit()
    session.refresh(obj)
    _emit_cashflow(session)
    cf = calculate_cashflow(session)
    return {"goal": obj, "analysis": analyze_goal(obj, cf)}


@router.patch("/{goal_id}/enabled")
def toggle_goal(goal_id: str, body: dict, session: Session = Depends(get_db)):
    obj = session.get(SavingsGoal, goal_id)
    if not obj:
        raise HTTPException(404)
    obj.enabled = bool(body.get("enabled", True))
    obj.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(obj)
    session.commit()
    session.refresh(obj)
    _emit_cashflow(session)
    return {"goal": obj}


@router.delete("/{goal_id}")
def delete_goal(goal_id: str, session: Session = Depends(get_db)):
    obj = session.get(SavingsGoal, goal_id)
    if not obj:
        raise HTTPException(404)
    session.delete(obj)
    session.commit()
    _emit_cashflow(session)
    return {"ok": True}
