"""Bill mark-paid and cashflow exclusion tests."""

from datetime import date, timedelta

from app.core.bill_payment import list_unpaid_past_due, mark_bill_paid, paid_keys
from app.core.bill_schedule import next_due_date_after_payment, sum_bills_due_in_period
from app.core.cashflow_engine import calculate_cashflow
from app.core.risk_engine import scan_risks
from app.db.models import Bill, IncomeSource


def test_mark_paid_excludes_from_cashflow(session):
    today = date.today()
    due = today + timedelta(days=5)
    session.add(
        IncomeSource(
            source_name="Job",
            amount=3000,
            next_pay_date=today + timedelta(days=14),
        )
    )
    bill = Bill(
        name="Electric",
        amount=150,
        due_date=due,
        recurrence="monthly",
        required=True,
    )
    session.add(bill)
    session.commit()
    session.refresh(bill)

    start = today
    end = today + timedelta(days=14)
    before = sum_bills_due_in_period(session, start, end)
    assert before == 150

    mark_bill_paid(session, bill, due)
    after = sum_bills_due_in_period(session, start, end)
    assert after == 0

    past_due = today - timedelta(days=10)
    mark_bill_paid(session, bill, past_due)
    unpaid = list_unpaid_past_due(session)
    assert not any(u["bill_id"] == bill.id and u["due_date"] == past_due.isoformat() for u in unpaid)


def test_paid_anchor_not_flagged_overdue(session):
    today = date.today()
    due = today - timedelta(days=30)
    bill = Bill(
        name="Internet",
        amount=80,
        due_date=due,
        recurrence="monthly",
        required=True,
    )
    session.add(bill)
    session.commit()
    session.refresh(bill)
    mark_bill_paid(session, bill, due)

    risks = scan_risks(session, calculate_cashflow(session, starting_cash=2000))
    overdue = [r for r in risks if r.get("code") == "bill_overdue" and r.get("entity_id") == bill.id]
    assert overdue == []


def test_unpaid_past_due_lists_occurrence(session):
    today = date.today()
    due = today - timedelta(days=5)
    bill = Bill(name="Water", amount=45, due_date=due, recurrence="monthly", required=True)
    session.add(bill)
    session.commit()
    session.refresh(bill)

    items = list_unpaid_past_due(session)
    assert any(i["name"] == "Water" for i in items)
    _, next_due = mark_bill_paid(session, bill, due)
    session.refresh(bill)
    assert not any(i["name"] == "Water" for i in list_unpaid_past_due(session))
    assert (bill.id, due) in paid_keys(session)
    assert next_due is not None
    assert next_due >= today
    assert bill.due_date == next_due


def test_mark_paid_advances_due_date_monthly(session):
    today = date.today()
    due = today - timedelta(days=45)
    bill = Bill(name="Rent", amount=1200, due_date=due, recurrence="monthly", required=True)
    session.add(bill)
    session.commit()
    session.refresh(bill)

    _, nxt = mark_bill_paid(session, bill, due)
    session.refresh(bill)
    expected = next_due_date_after_payment(due, "monthly", today)
    assert nxt == expected
    assert bill.due_date == expected
    assert bill.due_date >= today


def test_stale_anchor_syncs_after_paid_cycle(session):
    """Paid cycle still on bill.due_date (e.g. after edit) must advance on read."""
    today = date.today()
    due = today + timedelta(days=4)
    bill = Bill(name="Electric", amount=230, due_date=due, recurrence="monthly", required=True)
    session.add(bill)
    session.commit()
    session.refresh(bill)

    mark_bill_paid(session, bill, due)
    session.refresh(bill)
    assert bill.due_date > due

    # Simulate edit resetting anchor to the paid cycle.
    bill.due_date = due
    session.add(bill)
    session.commit()
    session.refresh(bill)

    from app.core.bill_payment import bill_payment_summary
    from app.core.bill_schedule import bill_events_in_range

    summary = bill_payment_summary(session, bill)
    session.refresh(bill)
    assert bill.due_date > due
    assert summary["due_date"] == bill.due_date.isoformat()

    events = bill_events_in_range([bill], today, today + timedelta(days=60), session)
    assert not any(e["due_date"] == due.isoformat() for e in events)
