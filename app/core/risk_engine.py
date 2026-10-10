"""Financial risk detection."""

from datetime import date, timedelta
from typing import Any

from sqlmodel import Session, select

from app.core.account_balances import account_totals
from app.core.bill_payment import bill_has_unpaid_past_due
from app.core.cashflow_engine import _effective_buffer, calculate_cashflow
from app.core.mortgage_schedule import mortgage_due_by_anchor, mortgage_is_late, mortgage_late_by_anchor
from app.db.models import Bill, BorrowerProfile, Debt, IncomeSource, MortgageAccount


def scan_risks(session: Session, cashflow: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    cf = cashflow or calculate_cashflow(session)
    risks: list[dict[str, Any]] = []
    today = date.today()

    if cf.get("risk_level") == "danger":
        risks.append({
            "code": "cashflow_shortfall",
            "severity": "critical",
            "message": "Projected shortfall before next paycheck",
        })

    sources = session.exec(select(IncomeSource)).all()
    next_pay_str = cf.get("next_pay_date")
    next_pay = date.fromisoformat(next_pay_str) if next_pay_str else today + timedelta(days=14)

    for bill in session.exec(select(Bill)).all():
        if bill.due_date and bill.due_date < next_pay:
            if not sources or all(
                s.next_pay_date and s.next_pay_date > bill.due_date for s in sources if s.next_pay_date
            ):
                risks.append({
                    "code": "bill_before_income",
                    "severity": "warning",
                    "message": f"Bill '{bill.name}' due before next paycheck",
                    "entity_id": bill.id,
                })
        if bill_has_unpaid_past_due(session, bill, today):
            risks.append({
                "code": "bill_overdue",
                "severity": "critical",
                "message": f"Bill '{bill.name}' has an unpaid past due date",
                "entity_id": bill.id,
            })

    for mortgage in session.exec(select(MortgageAccount)).all():
        due_by = mortgage_due_by_anchor(mortgage, today)
        late_by = mortgage_late_by_anchor(mortgage)
        if mortgage_is_late(mortgage, today):
            risks.append({
                "code": "mortgage_late",
                "severity": "critical",
                "message": (
                    f"Mortgage '{mortgage.lender}' is past late-by date "
                    f"({late_by.isoformat() if late_by else due_by.isoformat()})"
                ),
                "entity_id": mortgage.id,
            })
        elif due_by < today <= (late_by or due_by):
            risks.append({
                "code": "mortgage_grace_period",
                "severity": "warning",
                "message": (
                    f"Mortgage '{mortgage.lender}' is past due-by ({due_by.isoformat()}) "
                    f"but within grace until {late_by.isoformat() if late_by else 'N/A'}"
                ),
                "entity_id": mortgage.id,
            })
        elif due_by < next_pay and due_by >= today:
            if not sources or all(
                s.next_pay_date and s.next_pay_date > due_by for s in sources if s.next_pay_date
            ):
                risks.append({
                    "code": "mortgage_before_income",
                    "severity": "warning",
                    "message": f"Mortgage '{mortgage.lender}' due by {due_by.isoformat()} before next paycheck",
                    "entity_id": mortgage.id,
                })

    for debt in session.exec(select(Debt)).all():
        if debt.apr >= 20:
            risks.append({
                "code": "high_apr_debt",
                "severity": "warning",
                "message": f"High APR debt: {debt.creditor} at {debt.apr}%",
                "entity_id": debt.id,
            })

    profile = session.exec(select(BorrowerProfile)).first()
    buffer = _effective_buffer(session)
    savings_total = account_totals(session)["savings"]
    if profile and buffer > 0 and savings_total < buffer:
        risks.append({
            "code": "low_emergency_buffer",
            "severity": "warning",
            "message": "Emergency fund below minimum buffer",
        })

    debt_mins = cf.get("debt_minimums", 0)
    surplus = cf.get("projected_end_balance", 0)
    if debt_mins > surplus and cf.get("risk_level") != "safe":
        risks.append({
            "code": "debt_minimum_exceeds_surplus",
            "severity": "warning",
            "message": "Debt minimums exceed projected surplus",
        })

    return risks
