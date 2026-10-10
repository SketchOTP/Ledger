"""Permanent audit log for mark-paid, mark-received, and restore actions."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from sqlmodel import Session, select

from app.db.models import (
    Bill,
    BillInstance,
    CreditCard,
    CreditCardOccurrence,
    Debt,
    DebtOccurrence,
    IncomeSource,
    MiscOccurrence,
    MiscSpending,
    MortgageAccount,
    MortgageOccurrence,
    Paycheck,
    PaymentArchiveEvent,
    RentOccurrence,
    TravelExpense,
    TravelOccurrence,
)

MarkAction = Literal["marked_paid", "marked_received"]
ArchiveAction = Literal["marked_paid", "marked_received", "restored"]




def ensure_payment_archive_schema(session: Session) -> None:
    """Create audit table on per-user SQLite DBs when missing."""
    bind = session.get_bind()
    if bind is not None:
        PaymentArchiveEvent.__table__.create(bind, checkfirst=True)


def entity_slot(
    kind: str,
    scheduled_date: date,
    *,
    bill_id: str | None = None,
    debt_id: str | None = None,
    mortgage_id: str | None = None,
    credit_card_id: str | None = None,
    profile_id: str | None = None,
    travel_expense_id: str | None = None,
    misc_spending_id: str | None = None,
    income_source_id: str | None = None,
) -> str:
    ref = (
        bill_id
        or debt_id
        or mortgage_id
        or credit_card_id
        or profile_id
        or travel_expense_id
        or misc_spending_id
        or income_source_id
        or "unknown"
    )
    return f"{kind}:{ref}:{scheduled_date.isoformat()}"


def _entity_fields(**ids: str | None) -> dict[str, str | None]:
    return {k: v for k, v in ids.items() if v}


def log_payment_marked(
    session: Session,
    *,
    action: MarkAction,
    kind: str,
    name: str,
    amount: float,
    scheduled_date: date,
    marked_date: date | None = None,
    bill_id: str | None = None,
    debt_id: str | None = None,
    mortgage_id: str | None = None,
    credit_card_id: str | None = None,
    profile_id: str | None = None,
    travel_expense_id: str | None = None,
    misc_spending_id: str | None = None,
    income_source_id: str | None = None,
) -> PaymentArchiveEvent:
    ensure_payment_archive_schema(session)
    slot = entity_slot(
        kind,
        scheduled_date,
        bill_id=bill_id,
        debt_id=debt_id,
        mortgage_id=mortgage_id,
        credit_card_id=credit_card_id,
        profile_id=profile_id,
        travel_expense_id=travel_expense_id,
        misc_spending_id=misc_spending_id,
        income_source_id=income_source_id,
    )
    event = PaymentArchiveEvent(
        action=action,
        kind=kind,
        name=name,
        amount=round(float(amount), 2),
        scheduled_date=scheduled_date,
        marked_date=marked_date or scheduled_date,
        entity_slot=slot,
        **_entity_fields(
            bill_id=bill_id,
            debt_id=debt_id,
            mortgage_id=mortgage_id,
            credit_card_id=credit_card_id,
            profile_id=profile_id,
            travel_expense_id=travel_expense_id,
            misc_spending_id=misc_spending_id,
            income_source_id=income_source_id,
        ),
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def log_payment_restored(
    session: Session,
    *,
    kind: str,
    name: str,
    amount: float,
    scheduled_date: date,
    reverses_event_id: str | None,
    bill_id: str | None = None,
    debt_id: str | None = None,
    mortgage_id: str | None = None,
    credit_card_id: str | None = None,
    profile_id: str | None = None,
    travel_expense_id: str | None = None,
    misc_spending_id: str | None = None,
    income_source_id: str | None = None,
) -> PaymentArchiveEvent:
    ensure_payment_archive_schema(session)
    slot = entity_slot(
        kind,
        scheduled_date,
        bill_id=bill_id,
        debt_id=debt_id,
        mortgage_id=mortgage_id,
        credit_card_id=credit_card_id,
        profile_id=profile_id,
        travel_expense_id=travel_expense_id,
        misc_spending_id=misc_spending_id,
        income_source_id=income_source_id,
    )
    event = PaymentArchiveEvent(
        action="restored",
        kind=kind,
        name=name,
        amount=round(float(amount), 2),
        scheduled_date=scheduled_date,
        marked_date=date.today(),
        entity_slot=slot,
        reverses_event_id=reverses_event_id,
        **_entity_fields(
            bill_id=bill_id,
            debt_id=debt_id,
            mortgage_id=mortgage_id,
            credit_card_id=credit_card_id,
            profile_id=profile_id,
            travel_expense_id=travel_expense_id,
            misc_spending_id=misc_spending_id,
            income_source_id=income_source_id,
        ),
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def latest_mark_event_for_slot(session: Session, slot: str) -> PaymentArchiveEvent | None:
    rows = [
        row
        for row in session.exec(
            select(PaymentArchiveEvent).where(PaymentArchiveEvent.entity_slot == slot)
        ).all()
        if row.action in ("marked_paid", "marked_received")
    ]
    rows.sort(key=lambda row: row.created_at or datetime.min, reverse=True)
    return rows[0] if rows else None


def slot_is_restored(session: Session, slot: str) -> bool:
    latest = session.exec(
        select(PaymentArchiveEvent)
        .where(PaymentArchiveEvent.entity_slot == slot)
        .order_by(PaymentArchiveEvent.created_at.desc())
    ).first()
    return bool(latest and latest.action == "restored")


def is_mark_active(session: Session, event: PaymentArchiveEvent) -> bool:
    """Whether the operational mark is still in effect (not yet restored)."""
    if event.action not in ("marked_paid", "marked_received"):
        return False
    if event.bill_id:
        return (
            session.exec(
                select(BillInstance).where(
                    BillInstance.bill_id == event.bill_id,
                    BillInstance.due_date == event.scheduled_date,
                )
            ).first()
            is not None
        )
    if event.debt_id:
        return (
            session.exec(
                select(DebtOccurrence).where(
                    DebtOccurrence.debt_id == event.debt_id,
                    DebtOccurrence.due_date == event.scheduled_date,
                )
            ).first()
            is not None
        )
    if event.mortgage_id:
        return (
            session.exec(
                select(MortgageOccurrence).where(
                    MortgageOccurrence.mortgage_id == event.mortgage_id,
                    MortgageOccurrence.due_date == event.scheduled_date,
                )
            ).first()
            is not None
        )
    if event.credit_card_id:
        return (
            session.exec(
                select(CreditCardOccurrence).where(
                    CreditCardOccurrence.credit_card_id == event.credit_card_id,
                    CreditCardOccurrence.due_date == event.scheduled_date,
                )
            ).first()
            is not None
        )
    if event.profile_id:
        return (
            session.exec(
                select(RentOccurrence).where(
                    RentOccurrence.profile_id == event.profile_id,
                    RentOccurrence.due_date == event.scheduled_date,
                )
            ).first()
            is not None
        )
    if event.travel_expense_id:
        return (
            session.exec(
                select(TravelOccurrence).where(
                    TravelOccurrence.travel_expense_id == event.travel_expense_id,
                    TravelOccurrence.due_date == event.scheduled_date,
                )
            ).first()
            is not None
        )
    if event.misc_spending_id:
        return (
            session.exec(
                select(MiscOccurrence).where(
                    MiscOccurrence.misc_spending_id == event.misc_spending_id,
                    MiscOccurrence.due_date == event.scheduled_date,
                )
            ).first()
            is not None
        )
    if event.income_source_id:
        return (
            session.exec(
                select(Paycheck).where(
                    Paycheck.income_source_id == event.income_source_id,
                    Paycheck.received_date == event.scheduled_date,
                )
            ).first()
            is not None
        )
    return False


def backfill_legacy_archive_events(session: Session) -> int:
    """Create archive rows for pre-audit occurrences/paychecks (idempotent)."""
    ensure_payment_archive_schema(session)
    created = 0
    bills = {b.id: b for b in session.exec(select(Bill)).all()}
    debts = {d.id: d for d in session.exec(select(Debt)).all()}
    mortgages = {m.id: m for m in session.exec(select(MortgageAccount)).all()}
    cards = {c.id: c for c in session.exec(select(CreditCard)).all()}
    travel = {t.id: t for t in session.exec(select(TravelExpense)).all()}
    misc = {m.id: m for m in session.exec(select(MiscSpending)).all()}
    income = {s.id: s for s in session.exec(select(IncomeSource)).all()}

    def _has_mark(slot: str) -> bool:
        return any(
            row.action in ("marked_paid", "marked_received")
            for row in session.exec(
                select(PaymentArchiveEvent).where(PaymentArchiveEvent.entity_slot == slot)
            ).all()
        )

    for inst in session.exec(select(BillInstance)).all():
        bill = bills.get(inst.bill_id)
        slot = entity_slot("bill", inst.due_date, bill_id=inst.bill_id)
        if not _has_mark(slot):
            log_payment_marked(
                session,
                action="marked_paid",
                kind="bill",
                name=bill.name if bill else "Bill",
                amount=inst.amount,
                scheduled_date=inst.due_date,
                marked_date=inst.paid_date,
                bill_id=inst.bill_id,
            )
            created += 1

    for inst in session.exec(select(DebtOccurrence)).all():
        debt = debts.get(inst.debt_id)
        slot = entity_slot("debt", inst.due_date, debt_id=inst.debt_id)
        if not _has_mark(slot):
            log_payment_marked(
                session,
                action="marked_paid",
                kind="debt",
                name=debt.creditor if debt else "Debt",
                amount=inst.amount,
                scheduled_date=inst.due_date,
                marked_date=inst.paid_date,
                debt_id=inst.debt_id,
            )
            created += 1

    for inst in session.exec(select(MortgageOccurrence)).all():
        mortgage = mortgages.get(inst.mortgage_id)
        slot = entity_slot("mortgage", inst.due_date, mortgage_id=inst.mortgage_id)
        if not _has_mark(slot):
            log_payment_marked(
                session,
                action="marked_paid",
                kind="mortgage",
                name=f"{mortgage.lender} (mortgage)" if mortgage else "Mortgage",
                amount=inst.amount,
                scheduled_date=inst.due_date,
                marked_date=inst.paid_date,
                mortgage_id=inst.mortgage_id,
            )
            created += 1

    for inst in session.exec(select(CreditCardOccurrence)).all():
        card = cards.get(inst.credit_card_id)
        slot = entity_slot("credit_card", inst.due_date, credit_card_id=inst.credit_card_id)
        if not _has_mark(slot):
            log_payment_marked(
                session,
                action="marked_paid",
                kind="credit_card",
                name=(card.card_name or card.issuer) if card else "Credit card",
                amount=inst.amount,
                scheduled_date=inst.due_date,
                marked_date=inst.paid_date,
                credit_card_id=inst.credit_card_id,
            )
            created += 1

    for inst in session.exec(select(RentOccurrence)).all():
        slot = entity_slot("rent", inst.due_date, profile_id=inst.profile_id)
        if not _has_mark(slot):
            log_payment_marked(
                session,
                action="marked_paid",
                kind="rent",
                name="Rent",
                amount=inst.amount,
                scheduled_date=inst.due_date,
                marked_date=inst.paid_date,
                profile_id=inst.profile_id,
            )
            created += 1

    for inst in session.exec(select(TravelOccurrence)).all():
        expense = travel.get(inst.travel_expense_id)
        slot = entity_slot("travel", inst.due_date, travel_expense_id=inst.travel_expense_id)
        if not _has_mark(slot):
            log_payment_marked(
                session,
                action="marked_paid",
                kind="travel",
                name=(expense.label or expense.expense_type or "Travel") if expense else "Travel",
                amount=inst.amount,
                scheduled_date=inst.due_date,
                marked_date=inst.paid_date,
                travel_expense_id=inst.travel_expense_id,
            )
            created += 1

    for inst in session.exec(select(MiscOccurrence)).all():
        item = misc.get(inst.misc_spending_id)
        slot = entity_slot("misc", inst.due_date, misc_spending_id=inst.misc_spending_id)
        if not _has_mark(slot):
            log_payment_marked(
                session,
                action="marked_paid",
                kind="misc",
                name=item.name if item else "Misc",
                amount=inst.amount,
                scheduled_date=inst.due_date,
                marked_date=inst.paid_date,
                misc_spending_id=inst.misc_spending_id,
            )
            created += 1

    for paycheck in session.exec(select(Paycheck)).all():
        source = income.get(paycheck.income_source_id)
        slot = entity_slot(
            "income",
            paycheck.received_date,
            income_source_id=paycheck.income_source_id,
        )
        if not _has_mark(slot):
            log_payment_marked(
                session,
                action="marked_received",
                kind="income",
                name=source.source_name if source else "Paycheck",
                amount=paycheck.amount,
                scheduled_date=paycheck.received_date,
                marked_date=paycheck.received_date,
                income_source_id=paycheck.income_source_id,
            )
            created += 1

    return created


def archive_event_to_row(event: PaymentArchiveEvent, *, active: bool | None = None) -> dict[str, Any]:
    is_mark = event.action in ("marked_paid", "marked_received")
    if event.action == "restored":
        status = "restored"
        can_restore = False
    elif is_mark:
        is_active = bool(active)
        status = "active" if is_active else "restored"
        can_restore = is_active
    else:
        status = event.action
        can_restore = False
    return {
        "archive_id": event.id,
        "action": event.action,
        "status": status,
        "kind": event.kind,
        "name": event.name,
        "amount": round(float(event.amount), 2),
        "scheduled_date": event.scheduled_date.isoformat(),
        "due_date": event.scheduled_date.isoformat(),
        "marked_date": event.marked_date.isoformat() if event.marked_date else None,
        "marked_at": event.created_at.isoformat() if event.created_at else None,
        "reverses_event_id": event.reverses_event_id,
        "can_restore": can_restore,
        "bill_id": event.bill_id,
        "debt_id": event.debt_id,
        "mortgage_id": event.mortgage_id,
        "credit_card_id": event.credit_card_id,
        "profile_id": event.profile_id,
        "travel_expense_id": event.travel_expense_id,
        "misc_spending_id": event.misc_spending_id,
        "income_source_id": event.income_source_id,
    }
