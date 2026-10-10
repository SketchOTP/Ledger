"""Shared near-term obligation and income attention read model.

This is presentation-neutral financial truth consumed by both the V2 Today
composition and the retained V1 Command Center compatibility surface.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.core.income_payment import income_api_row, upcoming_income
from app.core.obligation_schedule import obligation_api_row, upcoming_obligations
from app.core.unpaid_obligations import list_all_unpaid_past_due

_PAST_DUE_ID_FIELDS = (
    "bill_id",
    "debt_id",
    "mortgage_id",
    "credit_card_id",
    "travel_expense_id",
    "misc_spending_id",
    "profile_id",
    "income_source_id",
)


def _attention_key(row: dict[str, Any]) -> str:
    due = str(row.get("due_date") or "")[:10]
    for field in _PAST_DUE_ID_FIELDS:
        if row.get(field):
            return f"{field}:{row[field]}:{due}"
    return f"{row.get('kind') or 'item'}:{row.get('name') or ''}:{due}"


def _past_due_attention_row(row: dict[str, Any]) -> dict[str, Any]:
    """Map an unpaid past-due obligation into a payable attention row."""
    return {
        "name": row.get("name"),
        "amount": row.get("amount"),
        "due_date": row.get("due_date"),
        "kind": row.get("kind") or "bill",
        "bill_id": row.get("bill_id"),
        "debt_id": row.get("debt_id"),
        "mortgage_id": row.get("mortgage_id"),
        "credit_card_id": row.get("credit_card_id"),
        "travel_expense_id": row.get("travel_expense_id"),
        "misc_spending_id": row.get("misc_spending_id"),
        "profile_id": row.get("profile_id"),
        "can_mark_paid": True,
        "overdue": True,
        "days_overdue": row.get("days_overdue"),
    }


def build_obligation_attention(
    session: Session, *, window_days: int, max_items: int = 20
) -> list[dict[str, Any]]:
    """Return unpaid past-due items plus literal-date upcoming income and obligations.

    Past-due and paid state use the same schedule/payment contracts as the
    rolling timeline, so callers cannot independently reinterpret dates.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    for row in (_past_due_attention_row(item) for item in list_all_unpaid_past_due(session)):
        key = _attention_key(row)
        if key not in seen:
            seen.add(key)
            out.append(row)

    upcoming = [
        obligation_api_row(row)
        for row in upcoming_obligations(session, days=window_days)[:max_items]
    ]
    upcoming += [income_api_row(row) for row in upcoming_income(session, days=window_days)]
    upcoming.sort(key=lambda row: (str(row.get("due_date") or ""), str(row.get("name") or "")))
    for row in upcoming:
        key = _attention_key(row)
        if key not in seen:
            seen.add(key)
            out.append(row)

    return out[:max_items]
