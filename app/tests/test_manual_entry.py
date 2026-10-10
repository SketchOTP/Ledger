"""Manual entry validation tests."""

import pytest
from fastapi.testclient import TestClient

from app.core.data_fix_queue import build_data_fix_queue
from app.core.entry_validation import (
    validate_bill,
    validate_credit_card,
    validate_debt,
)
from app.db.models import CreditCard
from app.main import app


@pytest.fixture
def client():
    from app.db.session import init_db

    init_db()
    return TestClient(app)


def test_quick_add_bill_validates_due_date():
    vr = validate_bill(
        {
            "name": "Rent",
            "amount": 1200,
            "recurrence": "monthly",
            "repeats": True,
            "due_date": None,
        }
    )
    assert vr.ok is False
    assert any("due date" in e.lower() for e in vr.errors)


def test_quick_add_bill_with_due_date_ok():
    vr = validate_bill(
        {
            "name": "Rent",
            "amount": 1200,
            "recurrence": "monthly",
            "repeats": True,
            "due_date": "2026-06-01",
        }
    )
    assert vr.ok is True


def test_credit_card_apr_over_100_rejected():
    vr = validate_credit_card(
        {
            "issuer": "Bank",
            "card_name": "Visa",
            "balance": 1000,
            "credit_limit": 5000,
            "apr": 150,
            "minimum_payment": 50,
        }
    )
    assert vr.ok is False
    assert any("100" in e for e in vr.errors)


def test_credit_card_balance_over_limit_warns():
    vr = validate_credit_card(
        {
            "issuer": "Bank",
            "balance": 6000,
            "credit_limit": 5000,
            "apr": 22,
            "minimum_payment": 150,
        }
    )
    assert vr.ok is True
    assert any("over the credit limit" in w for w in vr.warnings)


def test_debt_payment_higher_than_balance_warns():
    vr = validate_debt(
        {
            "creditor": "Loan",
            "balance": 500,
            "apr": 10,
            "minimum_payment": 800,
        }
    )
    assert vr.ok is True
    assert any("Minimum payment" in w for w in vr.warnings)


def test_create_bill_api_rejects_missing_due_date(client):
    res = client.post(
        "/api/bills",
        json={
            "name": "Electric",
            "amount": 100,
            "recurrence": "monthly",
            "repeats": True,
        },
    )
    assert res.status_code == 422


def test_save_ai_settings_mock_provider(client):
    res = client.post(
        "/api/settings/ai",
        json={
            "ai_provider": "mock",
            "ai_base_url": "",
            "ai_api_key": "",
            "ai_model": "mock",
            "ai_allow_remote_openai": False,
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["provider"] == "mock"


def test_create_credit_card_api_accepts_iso_due_date(client):
    res = client.post(
        "/api/profile/credit-cards",
        json={
            "issuer": "Chase",
            "card_name": "Freedom",
            "balance": 1200,
            "credit_limit": 5000,
            "apr": 22.5,
            "minimum_payment": 35,
            "due_date": "2026-06-15",
            "recurrence": "monthly",
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["due_date"] == "2026-06-15"


def test_create_mortgage_api_accepts_iso_start_date(client):
    res = client.post(
        "/api/profile/mortgages",
        json={
            "lender": "Test Bank",
            "current_balance": 200000,
            "original_balance": 200000,
            "interest_rate": 5.0,
            "monthly_payment": 1200,
            "escrow_amount": 300,
            "start_date": "2026-06-05",
            "property_value_estimate": 350000,
            "recurrence": "monthly",
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["account"]["start_date"] == "2026-06-05"
    assert res.json()["account"]["due_by_date"] == "2026-06-05"


def test_create_mortgage_api_due_by_and_late_by(client):
    res = client.post(
        "/api/profile/mortgages",
        json={
            "lender": "Grace Bank",
            "current_balance": 150000,
            "original_balance": 150000,
            "interest_rate": 5.5,
            "monthly_payment": 900,
            "escrow_amount": 200,
            "due_by_date": "2026-06-01",
            "late_by_date": "2026-06-12",
            "property_value_estimate": 300000,
            "recurrence": "monthly",
        },
    )
    assert res.status_code == 200, res.text
    acct = res.json()["account"]
    assert acct["due_by_date"] == "2026-06-01"
    assert acct["late_by_date"] == "2026-06-12"
    assert res.json()["analysis"]["payment_schedule"]["grace_days_after_due_by"] == 11


def test_upsert_borrower_mortgage_status_only(client):
    res = client.post("/api/profile/borrower", json={"housing_status": "mortgage"})
    assert res.status_code == 200
    assert res.json()["housing_status"] == "mortgage"


def test_upsert_borrower_mortgage_with_null_credit_score(client):
    """Housing patch must not require checking/savings balances."""
    res = client.post(
        "/api/profile/borrower",
        json={"housing_status": "mortgage", "credit_score": None},
    )
    assert res.status_code == 200


def test_create_credit_card_api_rejects_high_apr(client):
    res = client.post(
        "/api/profile/credit-cards",
        json={
            "issuer": "X",
            "card_name": "X",
            "balance": 1,
            "credit_limit": 1000,
            "apr": 120,
            "minimum_payment": 25,
        },
    )
    assert res.status_code == 422


def test_data_fix_queue_reflects_data_quality(session):
    session.add(
        CreditCard(
            issuer="Bad",
            card_name="Bad",
            balance=100,
            credit_limit=1000,
            apr=0,
            minimum_payment=10,
        )
    )
    session.commit()
    queue = build_data_fix_queue(session)
    assert queue["count"] >= 1
    checks = {item["check"] for item in queue["items"]}
    assert "missing_apr" in checks
    assert all(item.get("fix_path") for item in queue["items"])
