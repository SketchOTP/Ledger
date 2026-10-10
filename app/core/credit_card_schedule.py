"""Schedule credit card minimum payments (recurring until balance is paid off)."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlmodel import Session, select

from app.core.bill_schedule import bill_repeats, dates_in_range
from app.db.models import CreditCard


def cards_for_cashflow(session: Session) -> list[CreditCard]:
    """Cards with balance and minimum payment — stops scheduling when paid off."""
    out: list[CreditCard] = []
    for card in session.exec(select(CreditCard)).all():
        if float(card.balance or 0) <= 0:
            continue
        if float(card.minimum_payment or 0) <= 0:
            continue
        out.append(card)
    return out


def card_due_anchor(card: CreditCard | dict[str, Any], range_start: date) -> date:
    if isinstance(card, CreditCard):
        due = card.due_date
    else:
        due = card.get("due_date")
        if isinstance(due, str):
            due = date.fromisoformat(due[:10])
    if isinstance(due, date):
        return due
    return range_start.replace(day=min(15, 28))


def _card_recurrence(card: CreditCard | dict[str, Any]) -> str:
    if isinstance(card, CreditCard):
        return (card.recurrence or "monthly").lower()
    return (card.get("recurrence") or "monthly").lower()


def expand_card_occurrences(
    card: CreditCard | dict[str, Any],
    start: date,
    end: date,
) -> list[tuple[date, float]]:
    if isinstance(card, CreditCard):
        balance = float(card.balance or 0)
        pay = float(card.minimum_payment or 0)
    else:
        balance = float(card.get("balance", 0))
        pay = float(card.get("minimum_payment", 0))
    if balance <= 0 or pay <= 0:
        return []

    recurrence = _card_recurrence(card)
    anchor = card_due_anchor(card, start)
    if not bill_repeats(recurrence):
        if start <= anchor <= end:
            return [(anchor, min(pay, balance))]
        return []
    occurrences: list[tuple[date, float]] = []
    remaining = balance
    for due in dates_in_range(start, end, anchor, recurrence):
        if remaining <= 0:
            break
        amount = min(pay, remaining)
        occurrences.append((due, amount))
        remaining -= amount
    return occurrences


def sum_cards_due_in_period(session: Session, period_start: date, period_end: date) -> float:
    from app.core.credit_card_payment import sum_cards_due_in_period_unpaid

    return sum_cards_due_in_period_unpaid(session, period_start, period_end)


def credit_card_events_in_range(
    cards: list[CreditCard],
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for card in cards:
        for due, amount in expand_card_occurrences(card, start, end):
            name = f"{card.issuer} — {card.card_name} (min)"
            events.append(
                {
                    "date": due.isoformat(),
                    "type": "credit_card",
                    "name": name.strip(" —"),
                    "amount": amount,
                    "credit_card_id": card.id,
                }
            )
    events.sort(key=lambda e: e["date"])
    return events


def monthly_credit_card_minimums_total(session: Session) -> float:
    total = 0.0
    for card in cards_for_cashflow(session):
        pay = float(card.minimum_payment or 0)
        r = _card_recurrence(card)
        if not bill_repeats(r):
            continue
        if r == "weekly":
            total += pay * 52 / 12
        elif r == "biweekly":
            total += pay * 26 / 12
        else:
            total += pay
    return round(total, 2)
