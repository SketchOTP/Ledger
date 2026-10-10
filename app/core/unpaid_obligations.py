"""Unified past-due unpaid bills and debt minimums."""

from __future__ import annotations

from datetime import date

from sqlmodel import Session

from app.core.bill_payment import list_unpaid_past_due as list_unpaid_bills
from app.core.credit_card_payment import list_unpaid_credit_card_past_due
from app.core.debt_payment import list_unpaid_debt_past_due
from app.core.misc_payment import list_unpaid_misc_past_due
from app.core.mortgage_payment import list_unpaid_mortgage_past_due
from app.core.rent_payment import list_unpaid_rent_past_due


def list_all_unpaid_past_due(session: Session, as_of: date | None = None) -> list[dict]:
    today = as_of or date.today()
    items: list[dict] = []
    for row in list_unpaid_bills(session, today):
        items.append({**row, "kind": "bill", "name": row["name"]})
    for row in list_unpaid_debt_past_due(session, today):
        items.append(row)
    for row in list_unpaid_mortgage_past_due(session, today):
        items.append(row)
    for row in list_unpaid_credit_card_past_due(session, today):
        items.append(row)
    for row in list_unpaid_misc_past_due(session, today):
        items.append(row)
    for row in list_unpaid_rent_past_due(session, today):
        items.append(row)
    items.sort(key=lambda x: (x["due_date"], x.get("name", "")))
    return items
