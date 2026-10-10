"""My Money summary API tests."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    from app.db.session import init_db

    init_db()
    return TestClient(app)


def test_my_money_summary_returns_200(client):
    res = client.get("/api/my-money/summary")
    assert res.status_code == 200, res.text
    data = res.json()
    assert "sections" in data
    assert any(s["id"] == "bills" for s in data["sections"])
    assert not any(s["id"] in {"travel", "groceries"} for s in data["sections"])
    assert "travel" in data.get("monthly_obligations", {})
