"""Spending log API tests."""

from app.core.csv_io import import_csv
from app.db.models import Transaction


def test_create_transaction_via_csv_import(session):
    csv_text = """amount,category,description,transaction_date
15.50,groceries,Store,2026-06-23
8.00,dining,Lunch,2026-06-22
"""
    result = import_csv(session, "transactions", csv_text)
    assert result["imported"] == 2
    assert not result["errors"]
    rows = session.exec(__import__("sqlmodel").select(Transaction)).all()
    assert len(rows) == 2
    assert rows[0].category in ("groceries", "dining")
