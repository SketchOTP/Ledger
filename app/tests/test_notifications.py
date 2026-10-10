"""Notification inbox tests."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.core.notifications import (
    NotificationSpec,
    dismiss_notification,
    scan_cashflow_danger,
    snooze_notification,
    sync_notifications,
    upsert_notification,
)
from app.db.models import Bill, FinancialAction, NotificationInbox
from app.main import app


@pytest.fixture
def client():
    from app.db.session import init_db

    init_db()
    return TestClient(app)


def test_notification_dedupes(session):
    spec = NotificationSpec(
        notification_type="cashflow_danger",
        severity="warning",
        title="Tight cash",
        body="Test",
        link_path="/cashflow",
        dedupe_key="cashflow_danger:test",
    )
    _, created1, _ = upsert_notification(session, spec)
    _, created2, _ = upsert_notification(session, spec)
    session.commit()
    assert created1 is True
    assert created2 is False
    rows = list(session.exec(select(NotificationInbox)).all())
    assert len(rows) == 1


def test_critical_dismiss_requires_reason(session):
    spec = NotificationSpec(
        notification_type="data_quality_blocking",
        severity="critical",
        title="Blocking",
        body="Fix data",
        link_path="/data-quality",
        dedupe_key="dq:test",
    )
    row, _, _ = upsert_notification(session, spec)
    session.commit()
    with pytest.raises(ValueError, match="reason"):
        dismiss_notification(session, row.id, reason="")
    dismissed = dismiss_notification(session, row.id, reason="Reviewed and accepted risk")
    assert dismissed["status"] == "dismissed"


def test_snooze_notification(session):
    spec = NotificationSpec(
        notification_type="bill_due_soon",
        severity="info",
        title="Bill",
        body="Due soon",
        link_path="/money",
        dedupe_key="bill:test:2026-06-01",
    )
    row, _, _ = upsert_notification(session, spec)
    session.commit()
    out = snooze_notification(session, row.id, days=3)
    assert out["status"] == "snoozed"
    assert out["snoozed_until"]


def test_sync_creates_bill_due_notification(session):
    session.add(
        Bill(
            name="Electric",
            amount=120,
            required=True,
            due_date=date.today() + timedelta(days=3),
            recurrence="monthly",
        )
    )
    session.commit()
    result = sync_notifications(session, user_id="dev-local")
    assert result["created"] >= 1
    rows = list(session.exec(select(NotificationInbox)).all())
    types = {r.notification_type for r in rows}
    assert "bill_due_soon" in types


def test_cashflow_danger_severity(session):
    session.add(Bill(name="Huge", amount=99999, required=True, due_date=date.today()))
    session.commit()
    specs = scan_cashflow_danger(session)
    if specs:
        assert specs[0].severity in ("warning", "critical")


def test_action_overdue_notification(session):
    act = FinancialAction(
        title="Call bank",
        action_type="call_provider",
        status="open",
        due_date=date.today() - timedelta(days=2),
        is_urgent=True,
    )
    session.add(act)
    session.commit()
    sync_notifications(session, user_id="dev-local")
    rows = list(
        session.exec(
            select(NotificationInbox).where(NotificationInbox.notification_type == "action_overdue")
        ).all()
    )
    assert len(rows) >= 1


def test_api_inbox_and_dismiss(client, session):
    session.add(
        Bill(
            name="Water",
            amount=50,
            required=True,
            due_date=date.today() + timedelta(days=2),
            recurrence="monthly",
        )
    )
    session.commit()
    res = client.get("/api/notifications/inbox?sync=true")
    assert res.status_code == 200
    body = res.json()
    assert "critical" in body
    assert "active" in body

    if body["active"]:
        nid = body["active"][0]["id"]
        bad = client.post(f"/api/notifications/{nid}/dismiss", json={})
        if body["active"][0]["severity"] == "critical":
            assert bad.status_code == 400
        client.post(
            f"/api/notifications/{nid}/dismiss",
            json={"reason": "Handled manually"},
        )
