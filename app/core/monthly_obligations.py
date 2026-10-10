"""Monthly fixed outflows: bills + debt minimums + mortgage (no double-entry in Bills)."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.core.bill_schedule import monthly_obligation_total
from app.core.credit_card_schedule import monthly_credit_card_minimums_total
from app.core.debt_schedule import monthly_debt_minimums_total
from app.core.housing_schedule import monthly_housing_payments_total
from app.core.misc_schedule import monthly_misc_total


def monthly_obligations_breakdown(session: Session) -> dict[str, Any]:
    bills = monthly_obligation_total(session)
    debt_minimums = monthly_debt_minimums_total(session)
    mortgage_payments = monthly_housing_payments_total(session)
    credit_card_minimums = monthly_credit_card_minimums_total(session)
    misc = monthly_misc_total(session)
    total = round(
        bills + debt_minimums + mortgage_payments + credit_card_minimums + misc,
        2,
    )
    return {
        "bills": bills,
        "debt_minimums": debt_minimums,
        "mortgage_payments": mortgage_payments,
        "credit_card_minimums": credit_card_minimums,
        "travel": 0.0,
        "misc": misc,
        "total": total,
    }
