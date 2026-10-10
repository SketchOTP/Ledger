"""Helpers for summing liquid checking/savings accounts."""

from __future__ import annotations

from sqlmodel import Session, select

from app.db.models import BorrowerProfile, LiquidAccount


def account_totals(session: Session) -> dict[str, float]:
    profile = session.exec(select(BorrowerProfile)).first()
    checking = float(profile.checking_balance or 0) if profile else 0.0
    savings = float(profile.emergency_fund_balance or 0) if profile else 0.0

    rows = session.exec(select(LiquidAccount)).all()
    for row in rows:
        if not row.include_in_cashflow:
            continue
        t = (row.account_type or "checking").lower()
        if t == "savings":
            savings += float(row.balance or 0)
        else:
            checking += float(row.balance or 0)
    return {"checking": round(checking, 2), "savings": round(savings, 2)}
