"""Bill due_date advancement on payment."""

from datetime import date, timedelta

from app.core.bill_schedule import advance_anchor, next_due_date_after_payment


def test_next_due_catches_up_from_old_anchor():
    today = date.today()
    paid = today - timedelta(days=90)
    nxt = next_due_date_after_payment(paid, "monthly", today)
    assert nxt is not None
    assert nxt >= today
    assert nxt == advance_anchor(paid, "monthly") or nxt > paid


def test_next_due_weekly():
    today = date.today()
    paid = today - timedelta(days=3)
    nxt = next_due_date_after_payment(paid, "weekly", today)
    assert nxt is not None
    assert nxt >= today
