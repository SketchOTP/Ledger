"""Debt-to-income calculations."""

from typing import Any

from sqlmodel import Session, select

from app.core.income_aggregate import gross_monthly_from_sources
from app.core.rent_schedule import is_rent_housing
from app.db.models import BorrowerProfile, Debt, IncomeSource, MortgageAccount


def calculate_dti(session: Session) -> dict[str, Any]:
    profile = session.exec(select(BorrowerProfile)).first()
    sources = list(session.exec(select(IncomeSource)).all())
    gross_monthly = 0.0
    if profile:
        if profile.household_income > 0:
            gross_monthly = profile.household_income / 12
        elif profile.annual_income > 0:
            gross_monthly = profile.annual_income / 12

    if gross_monthly <= 0:
        gross_monthly = gross_monthly_from_sources(sources)

    debts = session.exec(select(Debt)).all()
    mortgages = session.exec(select(MortgageAccount)).all()
    monthly_debt = sum(d.minimum_payment for d in debts)
    if profile and is_rent_housing(session):
        housing = float(profile.monthly_rent or 0)
    else:
        housing = sum(float(m.monthly_payment or 0) + float(m.escrow_amount or 0) for m in mortgages)
    total_debt_payments = monthly_debt + housing

    housing_dti = (housing / gross_monthly * 100) if gross_monthly > 0 else 0
    total_dti = (total_debt_payments / gross_monthly * 100) if gross_monthly > 0 else 0

    if total_dti < 28:
        status = "healthy"
        why = "DTI within healthy range"
    elif total_dti < 36:
        status = "watch"
        why = "DTI approaching lender caution zone"
    elif total_dti < 43:
        status = "constrained"
        why = "DTI may limit new credit approval"
    else:
        status = "high-risk"
        why = "DTI in high-risk range for most lenders"

    if profile:
        profile.estimated_dti = round(total_dti, 2)
        session.add(profile)
        session.commit()

    return {
        "gross_monthly_income": round(gross_monthly, 2),
        "monthly_debt_payments": round(total_debt_payments, 2),
        "housing_dti": round(housing_dti, 2),
        "total_dti": round(total_dti, 2),
        "status": status,
        "why": why,
    }
