"""Bills list API returns rows sorted by next due date."""

from datetime import date, timedelta

from app.api.routes_bills import _bill_list_sort_key, list_bills
from app.db.models import Bill


def test_bill_list_sort_key_orders_soonest_first():
    today = date.today()
    rows = [
        {"name": "Zed", "due_date": (today + timedelta(days=20)).isoformat()},
        {"name": "Amy", "due_date": (today + timedelta(days=5)).isoformat()},
        {"name": "No due", "due_date": None},
    ]
    rows.sort(key=_bill_list_sort_key)
    assert rows[0]["name"] == "Amy"
    assert rows[1]["name"] == "Zed"
    assert rows[2]["name"] == "No due"


def test_list_bills_endpoint_sorted(session):
    today = date.today()
    session.add(
        Bill(name="Later", amount=10, required=True, due_date=today + timedelta(days=14))
    )
    session.add(
        Bill(name="Soon", amount=10, required=True, due_date=today + timedelta(days=2))
    )
    session.commit()
    names = [r["name"] for r in list_bills(session)]
    assert names.index("Soon") < names.index("Later")
