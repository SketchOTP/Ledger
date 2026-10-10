"""Aggregated My Money section summaries for the unified data-entry UI."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from app.core.account_balances import account_totals
from app.core.data_quality import assess_data_quality
from app.core.monthly_obligations import monthly_obligations_breakdown
from app.core.onboarding import (
    _assess_bills,
    _assess_cash_on_hand,
    _assess_credit_cards,
    _assess_debts,
    _assess_income,
    _assess_mortgage,
    get_declarations,
)
from app.db.models import (
    Bill,
    BorrowerProfile,
    CreditCard,
    Debt,
    IncomeSource,
    MiscSpending,
    MortgageAccount,
    SavingsGoal,
)


def build_my_money_summary(session: Session) -> dict[str, Any]:
    decl = get_declarations(session)
    borrower = session.exec(select(BorrowerProfile)).first()
    income = list(session.exec(select(IncomeSource)).all())
    bills = list(session.exec(select(Bill)).all())
    debts = list(session.exec(select(Debt)).all())
    mortgages = list(session.exec(select(MortgageAccount)).all())
    cards = list(session.exec(select(CreditCard)).all())
    misc_items = list(session.exec(select(MiscSpending).where(MiscSpending.active.is_(True))).all())
    savings_goals = list(session.exec(select(SavingsGoal)).all())

    sections = {
        "cash_on_hand": _assess_cash_on_hand(session),
        "income": _assess_income(session),
        "bills": _assess_bills(session, decl),
        "debts": _assess_debts(session, decl),
        "mortgage": _assess_mortgage(session, decl),
        "credit_cards": _assess_credit_cards(session, decl),
        "misc": _assess_misc(session, misc_items),
        "goals": _assess_goals(session, savings_goals),
    }

    dq = assess_data_quality(session, emit_events=False)
    domain_warnings = {d["domain"]: d for d in dq.get("domains", [])}

    out_sections: list[dict[str, Any]] = []
    for sid, assess in sections.items():
        dom = _section_domain_map(sid)
        dq_dom = domain_warnings.get(dom, {})
        out_sections.append(
            {
                "id": sid,
                "label": _labels.get(sid, sid),
                "complete": assess.get("complete", False),
                "skipped": assess.get("skipped", False),
                "score": assess.get("score", 0),
                "missing_fields": assess.get("missing_fields", []),
                "blocking_issues": assess.get("blocking_issues", []),
                "summary": _section_summary(
                    session,
                    sid,
                    borrower,
                    income,
                    bills,
                    debts,
                    mortgages,
                    cards,
                    misc_items,
                    savings_goals,
                ),
                "data_quality_score": dq_dom.get("score"),
                "data_quality_status": dq_dom.get("status"),
            }
        )

    obligations = monthly_obligations_breakdown(session)

    return {
        "sections": out_sections,
        "monthly_obligations": obligations,
        "counts": {
            "income": len(income),
            "bills": len(bills),
            "debts": len(debts),
            "mortgages": len(mortgages),
            "credit_cards": len(cards),
            "misc_spending": len(misc_items),
            "savings_goals": len(savings_goals),
        },
        "data_quality": {
            "overall_score": dq.get("overall_score"),
            "status": dq.get("status"),
        },
    }


_labels = {
    "cash_on_hand": "Cash on hand",
    "income": "Income",
    "bills": "Bills",
    "debts": "Debts",
    "mortgage": "Mortgage",
    "credit_cards": "Credit cards",
    "misc": "Miscellaneous",
    "goals": "Goals",
}


def _assess_misc(session: Session, items: list) -> dict[str, Any]:
    del session
    if not items:
        return {
            "complete": True,
            "skipped": False,
            "score": 100,
            "missing_fields": [],
            "blocking_issues": [],
        }
    return {
        "complete": True,
        "skipped": False,
        "score": 100,
        "missing_fields": [],
        "blocking_issues": [],
    }


def _assess_goals(session: Session, goals: list) -> dict[str, Any]:
    del session
    if not goals:
        return {
            "complete": True,
            "skipped": False,
            "score": 100,
            "missing_fields": [],
            "blocking_issues": [],
        }
    return {
        "complete": True,
        "skipped": False,
        "score": 100,
        "missing_fields": [],
        "blocking_issues": [],
    }


def _section_domain_map(section_id: str) -> str:
    return {
        "cash_on_hand": "borrower_profile",
        "income": "income",
    }.get(section_id, section_id)


def _section_summary(
    session: Session,
    sid: str,
    borrower: BorrowerProfile | None,
    income: list,
    bills: list,
    debts: list,
    mortgages: list,
    cards: list,
    misc_items: list | None = None,
    savings_goals: list | None = None,
) -> str:
    if sid == "cash_on_hand":
        if not borrower:
            return "No balances entered"
        totals = account_totals(session)
        return (
            f"Checking ${float(totals['checking']):,.0f} · "
            f"Savings ${float(totals['savings']):,.0f}"
        )
    if sid == "income":
        if not income:
            return "No paychecks"
        total = sum(i.amount for i in income)
        return f"{len(income)} source(s) · ~${total:,.0f} per period"
    if sid == "bills":
        if not bills:
            return "No bills"
        return f"{len(bills)} bill(s) · ${sum(b.amount for b in bills):,.0f} total per cycle"
    if sid == "debts":
        if not debts:
            return "None listed"
        return f"{len(debts)} debt(s) · ${sum(d.balance for d in debts):,.0f} owed"
    if sid == "mortgage":
        if borrower and (borrower.housing_status or "rent").lower() == "rent":
            rent = float(borrower.monthly_rent or 0)
            return f"Rent · ${rent:,.0f}/mo" if rent > 0 else "Rent — not set"
        if not mortgages:
            return "Mortgage — not set"
        m = mortgages[0]
        due = m.due_by_date or m.start_date
        late = m.late_by_date
        when = ""
        if due and late:
            when = f" · due by {due.isoformat()} · late after {late.isoformat()}"
        elif due:
            when = f" · due by {due.isoformat()}"
        return f"{m.lender} · ${float(m.current_balance or 0):,.0f} @ {m.interest_rate}%{when}"
    if sid == "credit_cards":
        if not cards:
            return "No cards"
        mins = sum(float(c.minimum_payment or 0) for c in cards if float(c.balance or 0) > 0)
        return f"{len(cards)} card(s) · ${mins:,.0f}/mo minimums · ${sum(c.balance for c in cards):,.0f} owed"
    if sid == "misc":
        from app.core.misc_schedule import monthly_misc_total

        count = len(misc_items or [])
        monthly = monthly_misc_total(session)
        if count == 0:
            return "No discretionary spending tracked"
        return f"{count} item(s) · ${monthly:,.0f}/mo"
    if sid == "goals":
        from app.core.goal_planner import build_goal_plan

        items = savings_goals or []
        if not items:
            return "No savings goals"
        enabled = sum(1 for g in items if g.enabled)
        plan = build_goal_plan(session)
        if enabled == 0:
            return f"{len(items)} goal(s) · all disabled"
        if plan.get("total_recommended", 0) > 0:
            return (
                f"{enabled} active · ${plan['total_recommended']:,.0f} safe to save now"
            )
        return f"{enabled} active · {plan.get('summary', 'tracking')[:60]}"
    return ""
