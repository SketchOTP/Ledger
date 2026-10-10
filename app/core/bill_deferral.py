"""One approved date change for one bill occurrence, never its recurring anchor."""

from __future__ import annotations

from datetime import date

from sqlmodel import Session, select

from app.core.bill_schedule import bill_repeats, next_due_date_after_payment, scheduled_bill_payments
from app.db.models import Bill, BillDeferral, BillInstance


def bill_deferrals(session: Session, bill_id: str | None = None) -> dict[tuple[str, date], BillDeferral]:
    query = select(BillDeferral)
    if bill_id is not None:
        query = query.where(BillDeferral.bill_id == bill_id)
    return {(row.bill_id, row.original_due_date): row for row in session.exec(query).all()}


def record_approved_bill_deferral(
    session: Session,
    bill: Bill,
    *,
    original_due_date: date,
    deferred_due_date: date,
    payee_approved: bool,
    as_of: date | None = None,
) -> BillDeferral:
    """Persist approval once; no edit/delete path is provided for the same cycle."""
    today = as_of or date.today()
    if not payee_approved:
        raise ValueError("Only record a new payment date after the payee approves it")
    if bill.due_date != original_due_date:
        raise ValueError("Choose this bill's current unpaid original due date")
    if deferred_due_date <= original_due_date or deferred_due_date < today:
        raise ValueError("The approved date must be later than the original date and not already past")
    if (bill.id, original_due_date) in bill_deferrals(session, bill.id):
        raise ValueError("This bill occurrence was already deferred once")
    paid = session.exec(
        select(BillInstance).where(
            BillInstance.bill_id == bill.id,
            BillInstance.due_date == original_due_date,
            BillInstance.paid.is_(True),
        )
    ).first()
    if paid:
        raise ValueError("A paid bill cannot be deferred")
    if bill.recurrence == "scheduled":
        payments = scheduled_bill_payments(bill)
        if original_due_date not in {due for due, _ in payments}:
            raise ValueError("This date is not in the payment plan")
        next_due = next((due for due, _ in payments if due > original_due_date), None)
    elif bill_repeats(bill.recurrence):
        next_due = next_due_date_after_payment(original_due_date, bill.recurrence, original_due_date)
    else:
        next_due = None
    if next_due and deferred_due_date >= next_due:
        raise ValueError("The new date must be before this bill's next regular payment")
    row = BillDeferral(
        bill_id=bill.id,
        original_due_date=original_due_date,
        deferred_due_date=deferred_due_date,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
