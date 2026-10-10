"""Per-occurrence credit card minimum payments via CreditCardOccurrence."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from app.core.bill_schedule import bill_repeats, next_due_date_after_payment
from app.core.credit_card_schedule import card_due_anchor, cards_for_cashflow, expand_card_occurrences
from app.core.schedule_overdue import anchor_overdue_tuple, overdue_anchor_amount
from app.db.models import CreditCard, CreditCardOccurrence


def paid_keys(session: Session) -> set[tuple[str, date]]:
    rows = session.exec(
        select(CreditCardOccurrence).where(CreditCardOccurrence.paid.is_(True))
    ).all()
    return {(r.credit_card_id, r.due_date) for r in rows if r.due_date}


def occurrence_is_paid(card_id: str, due: date, keys: set[tuple[str, date]]) -> bool:
    return (card_id, due) in keys


def _card_label(card: CreditCard) -> str:
    return f"{card.issuer} — {card.card_name} (min)".strip(" —")


def _overdue_anchor_amount(
    card: CreditCard,
    period_start: date,
    keys: set[tuple[str, date]],
) -> float:
    anchor = card_due_anchor(card, period_start)
    paid = occurrence_is_paid(card.id, anchor, keys)
    balance = float(card.balance or 0)
    pay = float(card.minimum_payment or 0)
    amount = min(pay, balance) if balance > 0 else pay
    return overdue_anchor_amount(anchor, period_start, amount, is_paid=paid)


def sum_cards_due_in_period_unpaid(
    session: Session,
    period_start: date,
    period_end: date,
) -> float:
    events = credit_card_events_in_range_unpaid(
        cards_for_cashflow(session), period_start, period_end, paid_keys(session), session
    )
    return round(sum(float(row["amount"]) for row in events), 2)


def credit_card_events_in_range_unpaid(
    cards: list[CreditCard],
    start: date,
    end: date,
    keys: set[tuple[str, date]],
    session: Session | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for card in cards:
        anchor = card_due_anchor(card, start)
        anchor_amt = _overdue_anchor_amount(card, start, keys)
        name = _card_label(card)
        if anchor_amt > 0:
            events.append(
                {
                    "date": anchor.isoformat(),
                    "type": "credit_card",
                    "name": name,
                    "amount": anchor_amt,
                    "credit_card_id": card.id,
                    "due_date": anchor.isoformat(),
                }
            )
        for due, amount in expand_card_occurrences(card, start, end):
            if occurrence_is_paid(card.id, due, keys):
                continue
            if due == anchor and anchor_amt > 0:
                continue
            events.append(
                {
                    "date": due.isoformat(),
                    "type": "credit_card",
                    "name": name,
                    "amount": amount,
                    "credit_card_id": card.id,
                    "due_date": due.isoformat(),
                }
            )
    if session is not None:
        from app.core.required_payment_deferral import apply_payment_deferrals

        return apply_payment_deferrals(session, "credit_card", events, end)
    events.sort(key=lambda e: e["date"])
    return events


def unpaid_occurrences_for_card(
    card: CreditCard,
    keys: set[tuple[str, date]],
    *,
    through: date,
    start: date | None = None,
) -> list[tuple[date, float]]:
    begin = start if start is not None else through - timedelta(days=400)
    out: list[tuple[date, float]] = []
    for due, amount in expand_card_occurrences(card, begin, through):
        if not occurrence_is_paid(card.id, due, keys):
            out.append((due, amount))
    return out


def list_unpaid_credit_card_past_due(session: Session, as_of: date | None = None) -> list[dict[str, Any]]:
    today = as_of or date.today()
    keys = paid_keys(session)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for card in cards_for_cashflow(session):
        anchor = card_due_anchor(card, today)
        balance = float(card.balance or 0)
        pay = float(card.minimum_payment or 0)
        amount = min(pay, balance) if balance > 0 else pay
        anchor_row = anchor_overdue_tuple(
            anchor,
            today,
            amount,
            is_paid=occurrence_is_paid(card.id, anchor, keys),
        )
        if anchor_row:
            due, amt = anchor_row
            out.append(
                {
                    "credit_card_id": card.id,
                    "name": _card_label(card),
                    "amount": amt,
                    "due_date": due.isoformat(),
                    "days_overdue": (today - due).days,
                    "recurrence": card.recurrence or "monthly",
                    "kind": "credit_card",
                }
            )
            seen.add((card.id, due.isoformat()))

        for due, amt in unpaid_occurrences_for_card(card, keys, through=today - timedelta(days=1)):
            if due >= today:
                continue
            key = (card.id, due.isoformat())
            if key in seen:
                continue
            out.append(
                {
                    "credit_card_id": card.id,
                    "name": _card_label(card),
                    "amount": amt,
                    "due_date": due.isoformat(),
                    "days_overdue": (today - due).days,
                    "recurrence": card.recurrence or "monthly",
                    "kind": "credit_card",
                }
            )
            seen.add(key)

    from app.core.required_payment_deferral import apply_past_due_deferrals

    return apply_past_due_deferrals(session, "credit_card", out, today)


def _advance_card_anchor(card: CreditCard, paid_due: date, paid_on: date) -> date | None:
    r = (card.recurrence or "monthly").lower()
    if not bill_repeats(r):
        card.recurrence = "none"
        card.due_date = None
        return None
    anchor = card.due_date or paid_due
    nxt = next_due_date_after_payment(anchor, r, paid_on)
    if nxt is not None:
        card.due_date = nxt
    return nxt


def mark_credit_card_paid(
    session: Session,
    card: CreditCard,
    due_date: date,
    *,
    paid_date: date | None = None,
    amount: float | None = None,
) -> tuple[CreditCardOccurrence, date | None]:
    from app.core.required_payment_deferral import original_payment_date

    due_date = original_payment_date(session, "credit_card", card.id, due_date)
    paid_on = paid_date or date.today()
    balance = float(card.balance or 0)
    pay = float(amount if amount is not None else card.minimum_payment or 0)
    amt = min(pay, balance) if balance > 0 else pay
    existing = session.exec(
        select(CreditCardOccurrence).where(
            CreditCardOccurrence.credit_card_id == card.id,
            CreditCardOccurrence.due_date == due_date,
        )
    ).first()
    if existing:
        existing.paid = True
        existing.paid_date = paid_on
        existing.amount = amt
        inst = existing
    else:
        inst = CreditCardOccurrence(
            credit_card_id=card.id,
            due_date=due_date,
            amount=amt,
            paid=True,
            paid_date=paid_on,
        )
    session.add(inst)

    card.balance = round(max(0.0, balance - amt), 2)
    if card.balance <= 0:
        card.balance = 0.0
        card.minimum_payment = 0.0
    next_due = _advance_card_anchor(card, due_date, paid_on)
    card.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(card)
    session.commit()
    session.refresh(inst)
    session.refresh(card)
    from app.core.payment_archive_log import log_payment_marked

    log_payment_marked(
        session,
        action="marked_paid",
        kind="credit_card",
        name=card.card_name or card.issuer,
        amount=amt,
        scheduled_date=due_date,
        marked_date=paid_on,
        credit_card_id=card.id,
    )
    return inst, next_due
