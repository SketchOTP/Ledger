"""Payee-approved one-time timing for debt, mortgage, and card payments."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlmodel import Session, select

from app.core.bill_schedule import bill_repeats, next_due_date_after_payment
from app.db.models import (
    CreditCard,
    CreditCardOccurrence,
    Debt,
    DebtOccurrence,
    MortgageAccount,
    MortgageOccurrence,
    RequiredPaymentDeferral,
)

KINDS = frozenset({"debt", "mortgage", "credit_card"})
ID_FIELDS = {"debt": "debt_id", "mortgage": "mortgage_id", "credit_card": "credit_card_id"}
ENTITIES = {"debt": Debt, "mortgage": MortgageAccount, "credit_card": CreditCard}
OCCURRENCES = {
    "debt": (DebtOccurrence, DebtOccurrence.debt_id),
    "mortgage": (MortgageOccurrence, MortgageOccurrence.mortgage_id),
    "credit_card": (CreditCardOccurrence, CreditCardOccurrence.credit_card_id),
}


def payment_deferrals(
    session: Session, kind: str | None = None, entity_id: str | None = None
) -> dict[tuple[str, str, date], RequiredPaymentDeferral]:
    query = select(RequiredPaymentDeferral)
    if kind is not None:
        query = query.where(RequiredPaymentDeferral.kind == kind)
    if entity_id is not None:
        query = query.where(RequiredPaymentDeferral.entity_id == entity_id)
    return {
        (row.kind, row.entity_id, row.original_due_date): row
        for row in session.exec(query).all()
    }


def current_payment_deferral(
    session: Session, kind: str, entity_id: str, original_due_date: date | None
) -> RequiredPaymentDeferral | None:
    if original_due_date is None:
        return None
    return payment_deferrals(session, kind, entity_id).get((kind, entity_id, original_due_date))


def original_payment_date(session: Session, kind: str, entity_id: str, effective: date) -> date:
    """Accept the displayed approved date while keeping the original occurrence key."""
    for row in payment_deferrals(session, kind, entity_id).values():
        if row.deferred_due_date == effective:
            return row.original_due_date
    return effective


def record_approved_payment_deferral(
    session: Session,
    kind: str,
    entity_id: str,
    *,
    original_due_date: date,
    deferred_due_date: date,
    payee_approved: bool,
    as_of: date | None = None,
) -> RequiredPaymentDeferral:
    today = as_of or date.today()
    if kind not in KINDS:
        raise ValueError("Unsupported payment type")
    entity = session.get(ENTITIES[kind], entity_id)
    if entity is None:
        raise ValueError("Payment account not found")
    anchor = (entity.due_by_date or entity.start_date) if kind == "mortgage" else entity.due_date
    if anchor is None or anchor != original_due_date:
        raise ValueError("Choose this payment's current unpaid original due date")
    if not payee_approved:
        raise ValueError("Only record a new payment date after the lender or payee approves it")
    if deferred_due_date <= original_due_date or deferred_due_date < today:
        raise ValueError("The approved date must be later and not already past")
    if current_payment_deferral(session, kind, entity_id, original_due_date):
        raise ValueError("This payment was already deferred once")
    occurrence_cls, id_col = OCCURRENCES[kind]
    paid = session.exec(
        select(occurrence_cls).where(
            id_col == entity_id,
            occurrence_cls.due_date == original_due_date,
            occurrence_cls.paid.is_(True),
        )
    ).first()
    if paid:
        raise ValueError("A paid payment cannot be deferred")
    recurrence = entity.recurrence or "monthly"
    if bill_repeats(recurrence):
        next_due = next_due_date_after_payment(original_due_date, recurrence, original_due_date)
        if next_due and deferred_due_date >= next_due:
            raise ValueError("The approved date must be before the next regular payment")
    row = RequiredPaymentDeferral(
        kind=kind,
        entity_id=entity_id,
        original_due_date=original_due_date,
        deferred_due_date=deferred_due_date,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def apply_payment_deferrals(
    session: Session, kind: str, events: list[dict[str, Any]], end: date
) -> list[dict[str, Any]]:
    dates = payment_deferrals(session, kind)
    out: list[dict[str, Any]] = []
    for event in events:
        entity_id = event.get(ID_FIELDS[kind])
        original = date.fromisoformat(str(event.get("due_date") or event["date"])[:10])
        row = dates.get((kind, entity_id, original))
        if row:
            event = {**event, "date": row.deferred_due_date.isoformat(), "deferred_once": True}
        if date.fromisoformat(event["date"][:10]) <= end:
            out.append(event)
    out.sort(key=lambda item: item["date"])
    return out


def apply_past_due_deferrals(
    session: Session, kind: str, rows: list[dict[str, Any]], today: date
) -> list[dict[str, Any]]:
    dates = payment_deferrals(session, kind)
    out = []
    for row in rows:
        original = date.fromisoformat(row["due_date"][:10])
        deferral = dates.get((kind, row[ID_FIELDS[kind]], original))
        effective = deferral.deferred_due_date if deferral else original
        if effective >= today:
            continue
        out.append({**row, "due_date": effective.isoformat(), "days_overdue": (today - effective).days})
    out.sort(key=lambda item: (item["due_date"], item.get("name", "")))
    return out
