"""Restore (unmark) paid obligations and received income — idempotent with audit trail."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import HTTPException
from sqlmodel import Session, select

from app.core.bill_payment import paid_keys, sync_bill_due_anchor
from app.core.bill_schedule import bill_repeats
from app.core.payment_archive_log import (
    entity_slot,
    latest_mark_event_for_slot,
    log_payment_restored,
    slot_is_restored,
)
from app.db.models import (
    Bill,
    BillInstance,
    BorrowerProfile,
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
    RentOccurrence,
    TravelExpense,
    TravelOccurrence,
)

RestoreOutcome = Literal["restored", "already_restored", "not_found"]


def _rollback_anchor(current: date | None, paid_due: date) -> date | None:
    if current and current > paid_due:
        return paid_due
    return current


def _already_or_missing(session: Session, slot: str, has_active: bool) -> RestoreOutcome:
    if has_active:
        return "restored"
    if slot_is_restored(session, slot):
        return "already_restored"
    return "not_found"


def _restore_income(session: Session, income_source_id: str, pay_date: date) -> RestoreOutcome:
    slot = entity_slot("income", pay_date, income_source_id=income_source_id)
    paycheck = session.exec(
        select(Paycheck).where(
            Paycheck.income_source_id == income_source_id,
            Paycheck.received_date == pay_date,
        )
    ).first()
    outcome = _already_or_missing(session, slot, paycheck is not None)
    if outcome != "restored":
        return outcome

    session.delete(paycheck)
    source = session.get(IncomeSource, income_source_id)
    if source:
        rolled = _rollback_anchor(source.next_pay_date, pay_date)
        if rolled != source.next_pay_date:
            source.next_pay_date = rolled
            source.updated_at = datetime.now(UTC).replace(tzinfo=None)
            session.add(source)
    session.commit()
    return "restored"


def _restore_bill(session: Session, bill_id: str, due_date: date) -> RestoreOutcome:
    slot = entity_slot("bill", due_date, bill_id=bill_id)
    inst = session.exec(
        select(BillInstance).where(
            BillInstance.bill_id == bill_id,
            BillInstance.due_date == due_date,
        )
    ).first()
    outcome = _already_or_missing(session, slot, inst is not None)
    if outcome != "restored":
        return outcome

    bill = session.get(Bill, bill_id)
    session.delete(inst)
    if bill and bill.recurrence == "scheduled":
        sync_bill_due_anchor(session, bill, paid_keys(session) - {(bill_id, due_date)})
    if bill and bill.due_date and bill_repeats(bill.recurrence or "monthly"):
        rolled = _rollback_anchor(bill.due_date, due_date)
        if rolled != bill.due_date:
            bill.due_date = rolled
            bill.updated_at = datetime.now(UTC).replace(tzinfo=None)
            session.add(bill)
    session.commit()
    return "restored"


def _restore_debt(session: Session, debt_id: str, due_date: date) -> RestoreOutcome:
    slot = entity_slot("debt", due_date, debt_id=debt_id)
    inst = session.exec(
        select(DebtOccurrence).where(
            DebtOccurrence.debt_id == debt_id,
            DebtOccurrence.due_date == due_date,
        )
    ).first()
    outcome = _already_or_missing(session, slot, inst is not None)
    if outcome != "restored":
        return outcome

    debt = session.get(Debt, debt_id)
    session.delete(inst)
    if debt and debt.due_date and bill_repeats(debt.recurrence or "monthly"):
        rolled = _rollback_anchor(debt.due_date, due_date)
        if rolled != debt.due_date:
            debt.due_date = rolled
            debt.updated_at = datetime.now(UTC).replace(tzinfo=None)
            session.add(debt)
    session.commit()
    return "restored"


def _restore_mortgage(session: Session, mortgage_id: str, due_date: date) -> RestoreOutcome:
    slot = entity_slot("mortgage", due_date, mortgage_id=mortgage_id)
    inst = session.exec(
        select(MortgageOccurrence).where(
            MortgageOccurrence.mortgage_id == mortgage_id,
            MortgageOccurrence.due_date == due_date,
        )
    ).first()
    outcome = _already_or_missing(session, slot, inst is not None)
    if outcome != "restored":
        return outcome

    mortgage = session.get(MortgageAccount, mortgage_id)
    session.delete(inst)
    if mortgage and bill_repeats(mortgage.recurrence or "monthly"):
        rolled = _rollback_anchor(mortgage.due_by_date, due_date)
        if rolled != mortgage.due_by_date:
            mortgage.due_by_date = rolled
            session.add(mortgage)
    session.commit()
    return "restored"


def _restore_credit_card(session: Session, card_id: str, due_date: date) -> RestoreOutcome:
    slot = entity_slot("credit_card", due_date, credit_card_id=card_id)
    inst = session.exec(
        select(CreditCardOccurrence).where(
            CreditCardOccurrence.credit_card_id == card_id,
            CreditCardOccurrence.due_date == due_date,
        )
    ).first()
    outcome = _already_or_missing(session, slot, inst is not None)
    if outcome != "restored":
        return outcome

    card = session.get(CreditCard, card_id)
    paid_amount = float(inst.amount or 0)
    session.delete(inst)
    if card:
        card.balance = round(float(card.balance or 0) + paid_amount, 2)
        rolled = _rollback_anchor(card.due_date, due_date)
        if rolled != card.due_date:
            card.due_date = rolled
        card.updated_at = datetime.now(UTC).replace(tzinfo=None)
        session.add(card)
    session.commit()
    return "restored"


def _restore_rent(session: Session, profile_id: str, due_date: date) -> RestoreOutcome:
    slot = entity_slot("rent", due_date, profile_id=profile_id)
    inst = session.exec(
        select(RentOccurrence).where(
            RentOccurrence.profile_id == profile_id,
            RentOccurrence.due_date == due_date,
        )
    ).first()
    outcome = _already_or_missing(session, slot, inst is not None)
    if outcome != "restored":
        return outcome

    profile = session.get(BorrowerProfile, profile_id)
    session.delete(inst)
    if profile:
        rolled = _rollback_anchor(profile.rent_due_date, due_date)
        if rolled != profile.rent_due_date:
            profile.rent_due_date = rolled
            profile.updated_at = datetime.now(UTC).replace(tzinfo=None)
            session.add(profile)
    session.commit()
    return "restored"


def _restore_travel(session: Session, travel_expense_id: str, due_date: date) -> RestoreOutcome:
    slot = entity_slot("travel", due_date, travel_expense_id=travel_expense_id)
    inst = session.exec(
        select(TravelOccurrence).where(
            TravelOccurrence.travel_expense_id == travel_expense_id,
            TravelOccurrence.due_date == due_date,
        )
    ).first()
    outcome = _already_or_missing(session, slot, inst is not None)
    if outcome != "restored":
        return outcome

    expense = session.get(TravelExpense, travel_expense_id)
    session.delete(inst)
    if expense and bill_repeats(expense.recurrence or "monthly"):
        rolled = _rollback_anchor(expense.due_date, due_date)
        if rolled != expense.due_date:
            expense.due_date = rolled
            expense.updated_at = datetime.now(UTC).replace(tzinfo=None)
            session.add(expense)
    session.commit()
    return "restored"


def _restore_misc(session: Session, misc_spending_id: str, due_date: date) -> RestoreOutcome:
    slot = entity_slot("misc", due_date, misc_spending_id=misc_spending_id)
    inst = session.exec(
        select(MiscOccurrence).where(
            MiscOccurrence.misc_spending_id == misc_spending_id,
            MiscOccurrence.due_date == due_date,
        )
    ).first()
    outcome = _already_or_missing(session, slot, inst is not None)
    if outcome != "restored":
        return outcome

    item = session.get(MiscSpending, misc_spending_id)
    session.delete(inst)
    if item and bill_repeats(item.recurrence or "monthly"):
        rolled = _rollback_anchor(item.due_date, due_date)
        if rolled != item.due_date:
            item.due_date = rolled
            item.updated_at = datetime.now(UTC).replace(tzinfo=None)
            session.add(item)
    session.commit()
    return "restored"


def _resolve_from_archive_id(session: Session, archive_id: str) -> dict[str, Any]:
    from app.db.models import PaymentArchiveEvent

    event = session.get(PaymentArchiveEvent, archive_id)
    if not event or event.action not in ("marked_paid", "marked_received"):
        raise HTTPException(404, "Archive mark event not found")
    payload: dict[str, Any] = {
        "due_date": event.scheduled_date,
        "kind": event.kind,
        "archive_id": archive_id,
    }
    for key in (
        "bill_id",
        "debt_id",
        "mortgage_id",
        "credit_card_id",
        "profile_id",
        "travel_expense_id",
        "misc_spending_id",
        "income_source_id",
    ):
        val = getattr(event, key, None)
        if val:
            payload[key] = val
    return payload


def restore_marked_payment(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Undo a mark-paid or mark-received action; append restore audit event."""
    if payload.get("archive_id"):
        payload = {**payload, **_resolve_from_archive_id(session, str(payload["archive_id"]))}

    due_raw = payload.get("due_date") or payload.get("pay_date") or payload.get("date")
    if not due_raw:
        raise HTTPException(400, "due_date is required")
    due_date = due_raw if isinstance(due_raw, date) else date.fromisoformat(str(due_raw)[:10])

    kind = payload.get("kind")
    outcome: RestoreOutcome
    restore_ctx: dict[str, Any] = {"scheduled_date": due_date}

    income_source_id = payload.get("income_source_id")
    if income_source_id or kind == "income":
        if not income_source_id:
            raise HTTPException(400, "income_source_id is required")
        outcome = _restore_income(session, income_source_id, due_date)
        kind = "income"
        restore_ctx["income_source_id"] = income_source_id
    elif payload.get("bill_id"):
        outcome = _restore_bill(session, str(payload["bill_id"]), due_date)
        kind = "bill"
        restore_ctx["bill_id"] = payload["bill_id"]
    elif payload.get("debt_id"):
        outcome = _restore_debt(session, str(payload["debt_id"]), due_date)
        kind = "debt"
        restore_ctx["debt_id"] = payload["debt_id"]
    elif payload.get("mortgage_id"):
        outcome = _restore_mortgage(session, str(payload["mortgage_id"]), due_date)
        kind = "mortgage"
        restore_ctx["mortgage_id"] = payload["mortgage_id"]
    elif payload.get("credit_card_id"):
        outcome = _restore_credit_card(session, str(payload["credit_card_id"]), due_date)
        kind = "credit_card"
        restore_ctx["credit_card_id"] = payload["credit_card_id"]
    elif payload.get("profile_id") or kind == "rent":
        pid = str(payload.get("profile_id") or "default")
        outcome = _restore_rent(session, pid, due_date)
        kind = "rent"
        restore_ctx["profile_id"] = pid
    elif payload.get("travel_expense_id"):
        outcome = _restore_travel(session, str(payload["travel_expense_id"]), due_date)
        kind = "travel"
        restore_ctx["travel_expense_id"] = payload["travel_expense_id"]
    elif payload.get("misc_spending_id"):
        outcome = _restore_misc(session, str(payload["misc_spending_id"]), due_date)
        kind = "misc"
        restore_ctx["misc_spending_id"] = payload["misc_spending_id"]
    else:
        raise HTTPException(400, "Could not identify payment type to restore")

    if outcome == "not_found":
        raise HTTPException(404, "Paid or received record not found")

    slot = entity_slot(
        kind,
        due_date,
        bill_id=restore_ctx.get("bill_id"),
        debt_id=restore_ctx.get("debt_id"),
        mortgage_id=restore_ctx.get("mortgage_id"),
        credit_card_id=restore_ctx.get("credit_card_id"),
        profile_id=restore_ctx.get("profile_id"),
        travel_expense_id=restore_ctx.get("travel_expense_id"),
        misc_spending_id=restore_ctx.get("misc_spending_id"),
        income_source_id=restore_ctx.get("income_source_id"),
    )
    mark_event = latest_mark_event_for_slot(session, slot)
    name = mark_event.name if mark_event else str(kind)
    amount = float(mark_event.amount if mark_event else 0)

    if outcome == "restored":
        log_payment_restored(
            session,
            kind=kind,
            name=name,
            amount=amount,
            scheduled_date=due_date,
            reverses_event_id=mark_event.id if mark_event else None,
            bill_id=restore_ctx.get("bill_id"),
            debt_id=restore_ctx.get("debt_id"),
            mortgage_id=restore_ctx.get("mortgage_id"),
            credit_card_id=restore_ctx.get("credit_card_id"),
            profile_id=restore_ctx.get("profile_id"),
            travel_expense_id=restore_ctx.get("travel_expense_id"),
            misc_spending_id=restore_ctx.get("misc_spending_id"),
            income_source_id=restore_ctx.get("income_source_id"),
        )

    return {
        "ok": True,
        "kind": kind,
        "restored_due_date": due_date.isoformat(),
        "already_restored": outcome == "already_restored",
    }
