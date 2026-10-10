"""Approved one-time lender dates for loan, mortgage, and card minimums."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_db
from app.core.bill_schedule import advance_anchor
from app.core.cashflow_engine import calculate_cashflow
from app.core.credit_card_payment import (
    credit_card_events_in_range_unpaid,
    list_unpaid_credit_card_past_due,
    mark_credit_card_paid,
)
from app.core.credit_card_payment import (
    paid_keys as card_paid_keys,
)
from app.core.debt_payment import (
    debt_events_in_range_unpaid,
    list_unpaid_debt_past_due,
    mark_debt_paid,
)
from app.core.debt_payment import (
    paid_keys as debt_paid_keys,
)
from app.core.liquidity_planner import build_liquidity_plan
from app.core.mortgage_payment import (
    list_unpaid_mortgage_past_due,
    mark_mortgage_paid,
    mortgage_events_in_range_unpaid,
)
from app.core.mortgage_payment import (
    paid_keys as mortgage_paid_keys,
)
from app.core.obligation_schedule import obligation_events_in_range
from app.core.required_payment_deferral import record_approved_payment_deferral
from app.db.models import (
    BorrowerProfile,
    CreditCard,
    Debt,
    IncomeSource,
    MortgageAccount,
    RequiredPaymentDeferral,
)
from app.main import app


@pytest.mark.parametrize("kind", ["debt", "mortgage", "credit_card"])
def test_approved_date_moves_only_current_payment_and_keeps_cycle(session, kind):
    today = date.today()
    original = today + timedelta(days=2)
    deferred = today + timedelta(days=5)
    entities = {
        "debt": Debt(
            creditor="Loan", balance=500, apr=10, minimum_payment=40, due_date=original, debt_type="personal_loan"
        ),
        "mortgage": MortgageAccount(
            lender="Home",
            original_balance=100_000,
            current_balance=80_000,
            interest_rate=5,
            monthly_payment=200,
            due_by_date=original,
        ),
        "credit_card": CreditCard(
            issuer="Bank",
            card_name="Card",
            balance=500,
            credit_limit=1000,
            apr=20,
            minimum_payment=25,
            due_date=original,
        ),
    }
    entity = entities[kind]
    session.add(entity)
    session.commit()
    with pytest.raises(ValueError, match="approves"):
        record_approved_payment_deferral(
            session,
            kind,
            entity.id,
            original_due_date=original,
            deferred_due_date=deferred,
            payee_approved=False,
        )
    record_approved_payment_deferral(
        session,
        kind,
        entity.id,
        original_due_date=original,
        deferred_due_date=deferred,
        payee_approved=True,
    )
    with pytest.raises(ValueError, match="already deferred"):
        record_approved_payment_deferral(
            session,
            kind,
            entity.id,
            original_due_date=original,
            deferred_due_date=deferred + timedelta(days=1),
            payee_approved=True,
        )
    functions = {
        "debt": (debt_events_in_range_unpaid, debt_paid_keys, list_unpaid_debt_past_due, mark_debt_paid, "debt_id"),
        "mortgage": (
            mortgage_events_in_range_unpaid,
            mortgage_paid_keys,
            list_unpaid_mortgage_past_due,
            mark_mortgage_paid,
            "mortgage_id",
        ),
        "credit_card": (
            credit_card_events_in_range_unpaid,
            card_paid_keys,
            list_unpaid_credit_card_past_due,
            mark_credit_card_paid,
            "credit_card_id",
        ),
    }
    events_fn, keys_fn, overdue_fn, mark_fn, id_field = functions[kind]
    events = events_fn([entity], today, advance_anchor(original, "monthly"), keys_fn(session), session)
    assert [(row["date"], row["due_date"]) for row in events] == [
        (deferred.isoformat(), original.isoformat()),
        (advance_anchor(original, "monthly").isoformat(),) * 2,
    ]
    assert events[0]["deferred_once"] is True
    assert not any(row[id_field] == entity.id for row in overdue_fn(session, original + timedelta(days=1)))
    overdue = overdue_fn(session, deferred + timedelta(days=1))
    assert any(row[id_field] == entity.id and row["due_date"] == deferred.isoformat() for row in overdue)
    mark_fn(session, entity, deferred)
    assert (entity.id, original) in keys_fn(session)
    anchor = entity.due_by_date if kind == "mortgage" else entity.due_date
    assert anchor == advance_anchor(original, "monthly")
    assert session.exec(select(RequiredPaymentDeferral)).first() is not None


@pytest.mark.parametrize(
    "kind,path,entity",
    [
        (
            "debt",
            "/api/debts",
            Debt(creditor="Loan", balance=500, apr=9, minimum_payment=40, debt_type="personal_loan"),
        ),
        (
            "mortgage",
            "/api/profile/mortgages",
            MortgageAccount(
                lender="Home", original_balance=100_000, current_balance=80_000, interest_rate=5, monthly_payment=200
            ),
        ),
        (
            "credit_card",
            "/api/profile/credit-cards",
            CreditCard(issuer="Bank", card_name="Card", balance=500, credit_limit=1000, apr=20, minimum_payment=25),
        ),
    ],
)
def test_approved_date_api_requires_confirmation(kind, path, entity):
    today = date.today()
    original = today + timedelta(days=2)
    deferred = today + timedelta(days=5)
    if kind == "mortgage":
        entity.due_by_date = original
    else:
        entity.due_date = original
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(entity)
        session.commit()
        app.dependency_overrides[get_db] = lambda: session
        try:
            client = TestClient(app)
            payload = {
                "original_due_date": original.isoformat(),
                "deferred_due_date": deferred.isoformat(),
                "payee_approved": False,
            }
            assert client.post(f"{path}/{entity.id}/approved-deferral", json=payload).status_code == 400
            payload["payee_approved"] = True
            response = client.post(f"{path}/{entity.id}/approved-deferral", json=payload)
            assert response.status_code == 200, response.text
            assert response.json()["approved_deferral"]["deferred_due_date"] == deferred.isoformat()
            assert client.post(f"{path}/{entity.id}/approved-deferral", json=payload).status_code == 400
        finally:
            app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize("kind", ["debt", "mortgage", "credit_card"])
def test_approved_date_replans_cash_and_today_in_same_cycle(session, kind):
    today = date.today()
    original = today + timedelta(days=2)
    deferred = today + timedelta(days=5)
    entities = {
        "debt": Debt(
            creditor="Loan",
            balance=500,
            apr=10,
            minimum_payment=100,
            due_date=original,
            debt_type="personal_loan",
        ),
        "mortgage": MortgageAccount(
            lender="Home",
            original_balance=100_000,
            current_balance=80_000,
            interest_rate=5,
            monthly_payment=100,
            due_by_date=original,
        ),
        "credit_card": CreditCard(
            issuer="Bank",
            card_name="Card",
            balance=500,
            credit_limit=1000,
            apr=20,
            minimum_payment=100,
            due_date=original,
        ),
    }
    entity = entities[kind]
    session.add(entity)
    session.add(BorrowerProfile(checking_balance=50, target_buffer=0))
    session.add(IncomeSource(source_name="Pay", amount=200, next_pay_date=today + timedelta(days=4)))
    session.commit()
    before = build_liquidity_plan(session)["money_state"]["cash_rescue"]
    assert before["first_gap"] is not None
    record_approved_payment_deferral(
        session,
        kind,
        entity.id,
        original_due_date=original,
        deferred_due_date=deferred,
        payee_approved=True,
    )
    after = build_liquidity_plan(session)["money_state"]["cash_rescue"]
    assert after["first_gap"] is None
    cashflow = calculate_cashflow(session)
    assert all(
        not (row.get("date") == original.isoformat() and row.get("type") == kind)
        for row in cashflow.get("upcoming_events", [])
    )
    schedule = obligation_events_in_range(session, today, today + timedelta(days=8))
    assert any(row["kind"] == kind and row["due_date"] == deferred.isoformat() for row in schedule)


@pytest.mark.parametrize("kind", ["debt", "mortgage", "credit_card"])
def test_payment_cannot_move_into_next_cycle_or_after_paid(session, kind):
    original = date.today() + timedelta(days=2)
    entities = {
        "debt": Debt(
            creditor="Loan",
            balance=500,
            apr=10,
            minimum_payment=40,
            due_date=original,
            debt_type="personal_loan",
        ),
        "mortgage": MortgageAccount(
            lender="Home",
            original_balance=100_000,
            current_balance=80_000,
            interest_rate=5,
            monthly_payment=200,
            due_by_date=original,
        ),
        "credit_card": CreditCard(
            issuer="Bank",
            card_name="Card",
            balance=500,
            credit_limit=1000,
            apr=20,
            minimum_payment=25,
            due_date=original,
        ),
    }
    entity = entities[kind]
    session.add(entity)
    session.commit()
    with pytest.raises(ValueError, match="before the next regular payment"):
        record_approved_payment_deferral(
            session,
            kind,
            entity.id,
            original_due_date=original,
            deferred_due_date=advance_anchor(original, "monthly"),
            payee_approved=True,
        )
    mark_fn = {
        "debt": mark_debt_paid,
        "mortgage": mark_mortgage_paid,
        "credit_card": mark_credit_card_paid,
    }[kind]
    mark_fn(session, entity, original)
    with pytest.raises(ValueError, match="current unpaid"):
        record_approved_payment_deferral(
            session,
            kind,
            entity.id,
            original_due_date=original,
            deferred_due_date=original + timedelta(days=3),
            payee_approved=True,
        )
