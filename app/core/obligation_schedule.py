"""All scheduled household obligations (generic misc replaces retired travel)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from app.core.bill_schedule import bill_events_in_range
from app.core.credit_card_schedule import cards_for_cashflow
from app.core.debt_schedule import debt_events_in_range, debts_for_cashflow
from app.core.housing_schedule import housing_events_in_range
from app.db.models import Bill, MiscSpending


def _event_row(
    *,
    name: str,
    amount: float,
    due_date: str,
    kind: str,
    required: bool = True,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "amount": amount,
        "due_date": due_date,
        "kind": kind,
        "required": required,
        "provenance": provenance or {"rule": f"{kind}_schedule"},
    }


def obligation_events_in_range(
    session: Session,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    """Merge canonical bill/housing/debt/card/misc events into one sorted list."""
    bills = list(session.exec(select(Bill)).all())
    debts = debts_for_cashflow(session)
    cards = cards_for_cashflow(session)
    from app.core.misc_payment import misc_events_in_range_unpaid
    from app.core.misc_payment import paid_keys as misc_paid_keys
    misc = list(session.exec(select(MiscSpending)).all())

    out: list[dict[str, Any]] = []

    for raw in bill_events_in_range(bills, start, end, session):
        due = str(raw.get("date", ""))[:10]
        out.append(
            _event_row(
                name=str(raw.get("name") or "Bill"),
                amount=float(raw.get("amount") or 0),
                due_date=due,
                kind="bill",
                required=bool(raw.get("required", True)),
                provenance={"rule": "bill_schedule", "type": "bill", "bill_id": raw.get("bill_id")},
            )
        )

    for raw in debt_events_in_range(debts, start, end, session):
        due = str(raw.get("date", ""))[:10]
        out.append(
            _event_row(
                name=str(raw.get("name") or "Debt payment"),
                amount=float(raw.get("amount") or 0),
                due_date=due,
                kind="debt",
                provenance={"rule": "debt_schedule", "type": "debt", "debt_id": raw.get("debt_id")},
            )
        )

    for raw in housing_events_in_range(session, start, end):
        due = str(raw.get("date", ""))[:10]
        kind = str(raw.get("type") or "mortgage")
        prov: dict[str, Any] = {"rule": "housing_schedule", "type": kind}
        if raw.get("mortgage_id"):
            prov["mortgage_id"] = raw.get("mortgage_id")
        if raw.get("profile_id"):
            prov["profile_id"] = raw.get("profile_id")
        out.append(
            _event_row(
                name=str(raw.get("name") or "Housing payment"),
                amount=float(raw.get("amount") or 0),
                due_date=due,
                kind=kind,
                provenance=prov,
            )
        )

    from app.core.credit_card_payment import credit_card_events_in_range_unpaid
    from app.core.credit_card_payment import paid_keys as card_paid_keys

    for raw in credit_card_events_in_range_unpaid(cards, start, end, card_paid_keys(session), session):
        due = str(raw.get("date", ""))[:10]
        out.append(
            _event_row(
                name=str(raw.get("name") or "Credit card minimum"),
                amount=float(raw.get("amount") or 0),
                due_date=due,
                kind="credit_card",
                provenance={
                    "rule": "credit_card_schedule",
                    "type": "credit_card",
                    "credit_card_id": raw.get("credit_card_id"),
                },
            )
        )

    for raw in misc_events_in_range_unpaid(misc, start, end, misc_paid_keys(session)):
        due = str(raw.get("date", ""))[:10]
        out.append(
            _event_row(
                name=str(raw.get("name") or "Misc spending"),
                amount=float(raw.get("amount") or 0),
                due_date=due,
                kind="misc",
                provenance={
                    "rule": "misc_schedule",
                    "type": "misc",
                    "misc_spending_id": raw.get("misc_spending_id"),
                },
            )
        )

    out.sort(key=lambda e: (e.get("due_date") or "", e.get("name") or ""))
    return out


def upcoming_obligations(session: Session, *, days: int = 30) -> list[dict[str, Any]]:
    """Obligations due from today through ``days`` ahead."""
    today = date.today()
    end = today + timedelta(days=days)
    return obligation_events_in_range(session, today, end)


def obligation_api_row(row: dict[str, Any]) -> dict[str, Any]:
    """Flatten obligation schedule row for API clients (mark-paid, lists)."""
    prov = row.get("provenance") or {}
    kind = str(row.get("kind") or prov.get("type") or "bill")
    bill_id = prov.get("bill_id")
    debt_id = prov.get("debt_id")
    mortgage_id = prov.get("mortgage_id")
    travel_expense_id = prov.get("travel_expense_id")
    misc_spending_id = prov.get("misc_spending_id")
    credit_card_id = prov.get("credit_card_id")
    profile_id = prov.get("profile_id")
    return {
        "name": row.get("name"),
        "amount": row.get("amount"),
        "due_date": row.get("due_date"),
        "kind": kind,
        "bill_id": bill_id,
        "debt_id": debt_id,
        "mortgage_id": mortgage_id,
        "travel_expense_id": travel_expense_id,
        "misc_spending_id": misc_spending_id,
        "credit_card_id": credit_card_id,
        "profile_id": profile_id,
        "can_mark_paid": bool(
            bill_id
            or debt_id
            or mortgage_id
            or travel_expense_id
            or misc_spending_id
            or credit_card_id
            or profile_id
        ),
    }
