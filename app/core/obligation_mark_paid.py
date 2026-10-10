"""Mark any scheduled obligation paid and advance its next due date."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import HTTPException
from sqlmodel import Session, select

from app.core.bill_payment import mark_bill_paid
from app.core.credit_card_payment import mark_credit_card_paid
from app.core.debt_payment import mark_debt_paid
from app.core.misc_payment import mark_misc_paid
from app.core.mortgage_payment import mark_mortgage_paid
from app.core.rent_payment import mark_rent_paid
from app.db.models import (
    Bill,
    BorrowerProfile,
    CreditCard,
    Debt,
    MiscSpending,
    MortgageAccount,
)


def mark_obligation_paid(
    session: Session,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Single entry point for timeline / command center mark-paid."""
    due_raw = payload.get("due_date") or payload.get("date")
    if not due_raw:
        raise HTTPException(400, "due_date is required")
    due_date = due_raw if isinstance(due_raw, date) else date.fromisoformat(str(due_raw)[:10])
    paid_date_raw = payload.get("paid_date")
    paid_date = (
        paid_date_raw
        if isinstance(paid_date_raw, date)
        else (date.fromisoformat(str(paid_date_raw)[:10]) if paid_date_raw else None)
    )
    amount = payload.get("amount")

    bill_id = payload.get("bill_id")
    debt_id = payload.get("debt_id")
    mortgage_id = payload.get("mortgage_id")
    misc_id = payload.get("misc_spending_id")
    card_id = payload.get("credit_card_id")
    profile_id = payload.get("profile_id")

    if bill_id:
        bill = session.get(Bill, bill_id)
        if not bill:
            raise HTTPException(404, "Bill not found")
        try:
            _, next_due = mark_bill_paid(session, bill, due_date, paid_date=paid_date, amount=amount)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "kind": "bill", "next_due_date": _iso(next_due)}

    if debt_id:
        debt = session.get(Debt, debt_id)
        if not debt:
            raise HTTPException(404, "Debt not found")
        _, next_due = mark_debt_paid(session, debt, due_date, paid_date=paid_date, amount=amount)
        return {"ok": True, "kind": "debt", "next_due_date": _iso(next_due)}

    if mortgage_id:
        mortgage = session.get(MortgageAccount, mortgage_id)
        if not mortgage:
            raise HTTPException(404, "Mortgage not found")
        _, next_due = mark_mortgage_paid(
            session, mortgage, due_date, paid_date=paid_date, amount=amount
        )
        return {"ok": True, "kind": "mortgage", "next_due_date": _iso(next_due)}

    if misc_id:
        item = session.get(MiscSpending, misc_id)
        if not item:
            raise HTTPException(404, "Misc spending not found")
        _, next_due = mark_misc_paid(session, item, due_date, paid_date=paid_date, amount=amount)
        return {"ok": True, "kind": "misc", "next_due_date": _iso(next_due)}

    if card_id:
        card = session.get(CreditCard, card_id)
        if not card:
            raise HTTPException(404, "Credit card not found")
        _, next_due = mark_credit_card_paid(
            session, card, due_date, paid_date=paid_date, amount=amount
        )
        return {"ok": True, "kind": "credit_card", "next_due_date": _iso(next_due)}

    if profile_id:
        profile = session.get(BorrowerProfile, profile_id)
        if not profile:
            profile = session.exec(select(BorrowerProfile)).first()
        if not profile:
            raise HTTPException(404, "Borrower profile not found")
        _, next_due = mark_rent_paid(
            session, profile, due_date, paid_date=paid_date, amount=amount
        )
        return {"ok": True, "kind": "rent", "next_due_date": _iso(next_due)}

    raise HTTPException(
        400,
        "Provide bill_id, debt_id, mortgage_id, misc_spending_id, "
        "credit_card_id, or profile_id (rent)",
    )


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None
