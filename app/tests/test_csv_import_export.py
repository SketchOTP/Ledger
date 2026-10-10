"""CSV import/export tests."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.core.csv_io import EXPORT_HEADERS, export_csv, import_csv
from app.db.models import Bill, Debt
from app.main import app


@pytest.fixture
def client():
    from app.db.session import init_db

    init_db()
    return TestClient(app)


def test_csv_export_includes_expected_headers(session):
    session.add(Bill(name="Rent", amount=1000, required=True))
    session.commit()
    content = export_csv(session, "bills")
    first_line = content.strip().splitlines()[0]
    assert first_line == ",".join(EXPORT_HEADERS["bills"])


def test_csv_import_creates_bills(session):
    csv_text = "name,amount,due_date,recurrence,required\nInternet,80,2026-06-15,monthly,true\n"
    result = import_csv(session, "bills", csv_text)
    assert result["created"] == 1
    bills = list(session.exec(select(Bill)).all())
    assert len(bills) == 1
    assert bills[0].name == "Internet"


def test_csv_import_creates_debts(session):
    csv_text = (
        "creditor,balance,apr,minimum_payment,due_date,debt_type\n"
        "CardCo,2000,19.9,75,2026-06-20,credit_card\n"
    )
    result = import_csv(session, "debts", csv_text)
    assert result["created"] == 1
    assert len(list(session.exec(select(Debt)).all())) == 1


def test_csv_import_rejects_bad_apr_in_credit_cards(session):
    csv_text = (
        "issuer,card_name,balance,credit_limit,apr,minimum_payment,due_date\n"
        "X,X,100,1000,200,25,\n"
    )
    result = import_csv(session, "credit_cards", csv_text)
    assert result["created"] == 0
    assert result["errors"]


def test_csv_export_api(client, session):
    session.add(Bill(name="Phone", amount=50, required=True))
    session.commit()
    res = client.get("/api/export/bills")
    assert res.status_code == 200
    assert "name,amount,due_date,recurrence,required" in res.text


def test_csv_import_api(client):
    csv_text = "name,amount,due_date,recurrence,required\nWater,30,2026-07-01,monthly,true\n"
    res = client.post("/api/import/bills", json={"content": csv_text})
    assert res.status_code == 200
    assert res.json()["created"] == 1


def test_export_headers_endpoint(client):
    res = client.get("/api/export/debts/headers")
    assert res.status_code == 200
    assert "creditor" in res.json()["headers"]
