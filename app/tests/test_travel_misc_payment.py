"""Generic scheduled-spending payment tests after Travel retirement."""

from datetime import date, timedelta

from sqlmodel import select

from app.core.misc_payment import mark_misc_paid
from app.core.obligation_schedule import obligation_api_row
from app.db.models import MiscSpending


def test_mark_misc_paid_excludes_from_timeline(session):
    today = date.today()
    session.add(
        MiscSpending(
            name="Claude",
            category="misc",
            amount=20,
            due_date=today + timedelta(days=5),
            recurrence="monthly",
        )
    )
    session.commit()
    item = session.exec(select(MiscSpending)).first()
    assert item is not None
    due = today + timedelta(days=5)
    mark_misc_paid(session, item, due)
    api_row = obligation_api_row(
        {
            "name": "Claude",
            "amount": 20,
            "due_date": due.isoformat(),
            "kind": "misc",
            "provenance": {"misc_spending_id": item.id},
        }
    )
    assert api_row["can_mark_paid"] is True
    assert api_row["misc_spending_id"] == item.id
