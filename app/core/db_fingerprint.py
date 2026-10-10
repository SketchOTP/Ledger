"""Small read-only database fingerprints used by non-mutating regression checks."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from app.db.models import Bill, Debt, IncomeSource


def capture_db_fingerprint(session: Session) -> dict[str, Any]:
    """Return the financial rows relevant to a read-only mutation check."""
    return {
        "debts": [(d.id, d.balance, d.minimum_payment) for d in session.exec(select(Debt)).all()],
        "bills": [(b.id, b.amount) for b in session.exec(select(Bill)).all()],
        "income": [(i.id, i.amount) for i in session.exec(select(IncomeSource)).all()],
    }
