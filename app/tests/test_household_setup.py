"""Household setup status API tests."""

import pytest
from fastapi.testclient import TestClient

from app.core.household_setup import build_household_setup_status
from app.db.models import Bill, IncomeSource
from app.main import app


@pytest.fixture
def client():
    from app.db.session import init_db

    init_db()
    return TestClient(app)


def test_household_setup_status_empty(session):
    payload = build_household_setup_status(session)
    assert payload["completion_score"] < 100
    assert payload["checklist"]
    assert payload["features"]
    assert len(payload["import_templates"]) == 4


def test_household_setup_api(client):
    res = client.get("/api/household-setup")
    assert res.status_code == 200
    body = res.json()
    assert "feature_blockers" in body
    assert body["guided_flow_path"] == "/household-setup"


def test_import_template_download(client):
    res = client.get("/api/household-setup/templates/bills")
    assert res.status_code == 200
    assert "text/csv" in res.headers.get("content-type", "")


def test_setup_improves_with_income(session):
    session.add(
        IncomeSource(
            source_name="Job",
            amount=3000,
            pay_frequency="biweekly",
            next_pay_date=__import__("datetime").date.today(),
        )
    )
    session.add(Bill(name="Rent", amount=1200, required=True, due_date=__import__("datetime").date.today()))
    session.commit()
    before = build_household_setup_status(session)
    assert before["completion_score"] > 0
