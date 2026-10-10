"""Spending and budget API."""

import math
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlmodel import Session, select

from app.api.deps import get_db
from app.core.budget_engine import _is_weekly_groceries, check_budget_exceeded, sync_category_spending
from app.core.cashflow_engine import calculate_cashflow
from app.core.csv_io import import_csv
from app.core.v2_opportunities import refresh_household_decision_dependents
from app.db.models import BudgetCategory, Transaction
from app.events.bus import event_bus
from app.events.types import Event, EventType

router = APIRouter(prefix="/api/spending", tags=["spending"])


class CsvImportBody(BaseModel):
    content: str


class PlannedSpendingBody(BaseModel):
    budget_category_id: str
    planned_date: date
    transaction_date: date
    amount: float


@router.get("/transactions")
def list_transactions(session: Session = Depends(get_db)):
    rows = session.exec(select(Transaction).order_by(Transaction.transaction_date.desc())).all()  # type: ignore[arg-type]
    return rows


@router.get("/pending-checking")
def pending_checking(session: Session = Depends(get_db)):
    pending = session.exec(select(Transaction).where(
        or_(Transaction.planned_budget_category_id.is_not(None), func.lower(Transaction.category) == "discretionary"),
        Transaction.reflected_in_checking.is_(False),
    )).all()
    return {"count": len(pending)}


@router.post("/transactions")
def create_transaction(data: dict, session: Session = Depends(get_db)):
    from datetime import date

    amount = data.get("amount")
    if (not isinstance(amount, (int, float)) or not math.isfinite(amount)
            or (str(data.get("category") or "").strip().casefold() == "discretionary" and amount <= 0)):
        raise HTTPException(400, "Enter a positive purchase amount.")
    obj = Transaction(
        amount=amount,
        category=data["category"],
        description=data.get("description", ""),
        transaction_date=date.fromisoformat(data["transaction_date"]),
    )
    session.add(obj)
    session.commit()
    sync_category_spending(session)
    refresh_household_decision_dependents(session, {"cashflow"}, cashflow=calculate_cashflow(session))
    return obj


@router.post("/record-planned")
def record_planned_spending(body: PlannedSpendingBody, session: Session = Depends(get_db)):
    """Record a real purchase; checking is changed separately by the owner."""
    category = session.get(BudgetCategory, body.budget_category_id)
    if category is None or not (_is_weekly_groceries(category) or category.period == "day"):
        raise HTTPException(404, "Planned spending item not found.")
    if not math.isfinite(body.amount) or not 0 < body.amount <= 1_000_000:
        raise HTTPException(400, "Enter the amount you actually spent.")
    today = date.today()
    if body.transaction_date > today or abs((body.planned_date - body.transaction_date).days) > 7:
        raise HTTPException(400, "Enter a purchase date within a week of this plan.")
    if not today - timedelta(days=7) <= body.planned_date <= today:
        raise HTTPException(400, "Choose a current planned spending item.")
    if _is_weekly_groceries(category):
        if body.planned_date.weekday() != category.planned_weekday:
            raise HTTPException(400, "Choose the planned grocery day.")
        existing = session.exec(select(Transaction).where(
            Transaction.planned_budget_category_id == category.id,
            Transaction.planned_spending_date == body.planned_date,
        )).first()
        if existing is not None:
            raise HTTPException(409, "This grocery trip is already recorded.")
    elif body.planned_date != today:
        raise HTTPException(400, "Record everyday spending for today.")
    obj = Transaction(
        amount=round(body.amount, 2), category=category.name,
        description="Recorded from Today", transaction_date=body.transaction_date,
        planned_budget_category_id=category.id,
        planned_spending_date=body.planned_date,
        reflected_in_checking=False,
    )
    session.add(obj)
    session.commit()
    sync_category_spending(session)
    refresh_household_decision_dependents(session, {"cashflow"}, cashflow=calculate_cashflow(session))
    session.refresh(obj)
    return obj


@router.post("/transactions/import")
def import_transactions_csv(body: CsvImportBody, session: Session = Depends(get_db)):
    try:
        result = import_csv(session, "transactions", body.content)
        refresh_household_decision_dependents(session, {"cashflow"}, cashflow=calculate_cashflow(session))
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/budget")
def list_budget(session: Session = Depends(get_db)):
    return session.exec(select(BudgetCategory)).all()


@router.post("/budget")
def create_budget(data: dict, session: Session = Depends(get_db)):
    name, amount, period, limit = _budget_input(data)
    weekday = _budget_weekday(data)
    obj = BudgetCategory(name=name, usual_amount=amount, period=period, monthly_limit=limit,
                         planned_weekday=weekday)
    session.add(obj)
    session.commit()
    sync_category_spending(session)
    refresh_household_decision_dependents(session, {"cashflow"}, cashflow=calculate_cashflow(session))
    session.refresh(obj)
    return obj


def _budget_input(data: dict) -> tuple[str, float, str, float]:
    name = str(data.get("name") or "").strip()
    period = str(data.get("period") or "month").lower()
    try:
        amount = float(data.get("usual_amount", data.get("monthly_limit")))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Enter a spending amount.") from exc
    if period not in {"day", "week", "month"}:
        raise HTTPException(status_code=400, detail="Choose per day, week, or month.")
    factor = {"day": 365.25 / 12, "week": 365.25 / 84, "month": 1}[period]
    limit = round(amount * factor, 2)
    if not name or len(name) > 80 or not math.isfinite(amount) or not 0 <= amount or limit > 1_000_000:
        raise HTTPException(status_code=400, detail="Enter a name and a valid spending amount.")
    return name, round(amount, 2), period, limit


def _budget_weekday(data: dict) -> int:
    try:
        weekday = int(data.get("planned_weekday", 5))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Choose a day of the week.") from exc
    if weekday not in range(7):
        raise HTTPException(status_code=400, detail="Choose a day of the week.")
    return weekday


@router.patch("/budget/{budget_id}")
def update_budget(budget_id: str, data: dict, session: Session = Depends(get_db)):
    obj = session.get(BudgetCategory, budget_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Spending plan not found.")
    obj.name, obj.usual_amount, obj.period, obj.monthly_limit = _budget_input(data)
    obj.planned_weekday = _budget_weekday(data)
    session.add(obj)
    session.commit()
    sync_category_spending(session)
    refresh_household_decision_dependents(session, {"cashflow"}, cashflow=calculate_cashflow(session))
    session.refresh(obj)
    return obj


@router.delete("/budget/{budget_id}")
def delete_budget(budget_id: str, session: Session = Depends(get_db)):
    obj = session.get(BudgetCategory, budget_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Spending plan not found.")
    session.delete(obj)
    session.commit()
    refresh_household_decision_dependents(session, {"cashflow"}, cashflow=calculate_cashflow(session))
    return {"ok": True}


@router.get("/budget/exceeded")
def budget_exceeded(session: Session = Depends(get_db)):
    exceeded = check_budget_exceeded(session)
    for e in exceeded:
        event_bus.emit(Event(
            event_type=EventType.BUDGET_CATEGORY_EXCEEDED.value,
            payload=e,
        ))
    return exceeded
