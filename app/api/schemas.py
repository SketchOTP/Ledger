"""Shared API schemas."""

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


class ScheduledBillPayment(BaseModel):
    due_date: date
    amount: float = Field(gt=0, le=1_000_000)


class IncomeSourceCreate(BaseModel):
    source_name: str
    amount: float
    pay_frequency: str = "biweekly"
    next_pay_date: Optional[date] = None
    owner: str = "primary"
    notes: str = ""


class BillMarkPaid(BaseModel):
    due_date: date
    paid_date: Optional[date] = None
    amount: Optional[float] = None


class BillCreate(BaseModel):
    name: str
    amount: float
    due_date: Optional[date] = None
    recurrence: str = "monthly"  # weekly | biweekly | monthly | quarterly | annual | once
    payments: list[ScheduledBillPayment] | None = None
    repeats: Optional[bool] = None  # if set, maps to monthly vs once
    priority: int = 1
    autopay: bool = False
    category: str = "general"
    required: bool = True


class DebtCreate(BaseModel):
    creditor: str
    balance: float
    apr: float
    minimum_payment: float
    due_date: Optional[date] = None
    recurrence: str = "monthly"
    debt_type: str = "credit_card"
    payoff_priority: int = 1
    hardship_notes: str = ""


class MiscSpendingCreate(BaseModel):
    name: str
    category: str = "other"
    amount: float
    due_date: Optional[date] = None
    recurrence: str = "monthly"
    repeats: Optional[bool] = None


class SavingsGoalCreate(BaseModel):
    name: str
    target_amount: float
    saved_amount: float = 0.0
    due_date: Optional[date] = None
    enabled: bool = True
    linked_account_id: Optional[str] = None
    notes: str = ""


class RecommendationAction(BaseModel):
    status: str  # accepted, dismissed, snoozed
