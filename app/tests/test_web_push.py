"""Web Push subscription and delivery tests."""

from datetime import date
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pywebpush import WebPushException
from sqlmodel import select

from app.config import settings
from app.core.notifications import sync_notifications
from app.core.web_push import (
    disable_subscription,
    export_vapid_keypair,
    push_payload_for_notification,
    send_push_for_notification,
    upsert_subscription,
)
from app.db.models import Bill, NotificationDeliveryAttempt, NotificationInbox, PushSubscription
from app.main import app


def _vapid_fixture(monkeypatch):
    public_key, private_key = export_vapid_keypair()
    monkeypatch.setattr(settings, "vapid_public_key", public_key)
    monkeypatch.setattr(settings, "vapid_private_key", private_key)
    monkeypatch.setattr(settings, "vapid_subject", "mailto:test@ledgermind.local")
    return public_key, private_key


@pytest.fixture
def client():
    from app.db.session import init_db

    init_db()
    return TestClient(app)


@pytest.fixture
def vapid(monkeypatch):
    return _vapid_fixture(monkeypatch)


def test_subscription_upsert_updates_keys(session, vapid):
    sub1 = upsert_subscription(
        session,
        user_id="dev-local",
        endpoint="https://push.example/1",
        p256dh_key="key-a",
        auth_key="auth-a",
    )
    session.commit()
    sub2 = upsert_subscription(
        session,
        user_id="dev-local",
        endpoint="https://push.example/1",
        p256dh_key="key-b",
        auth_key="auth-b",
    )
    session.commit()
    assert sub1.id == sub2.id
    assert sub2.p256dh_key == "key-b"
    assert sub2.auth_key == "auth-b"


def test_push_payload_for_bill_notification(session):
    row = NotificationInbox(
        notification_type="bill_due_soon",
        title="Due today: Electric",
        body="$120 due today",
        link_path="/money",
        dedupe_key="bill_due_soon:bill:Electric:2026-06-23",
        severity="critical",
        payload_json='{"due_date":"2026-06-23","amount":120}',
    )
    payload = push_payload_for_notification(row)
    assert payload["title"] == "Due today: Electric"
    assert payload["requireInteraction"] is True
    assert payload["notificationId"] == row.id


def test_stale_subscription_disabled_on_410(session, vapid, monkeypatch):
    upsert_subscription(
        session,
        user_id="dev-local",
        endpoint="https://push.example/stale",
        p256dh_key="p256",
        auth_key="auth",
    )
    row = NotificationInbox(
        notification_type="bill_due_soon",
        title="Bill",
        body="Due",
        link_path="/money",
        dedupe_key="bill:test:stale",
        severity="warning",
    )
    session.add(row)
    session.commit()
    session.refresh(row)

    response = MagicMock()
    response.status_code = 410
    exc = WebPushException("gone", response=response)

    def fake_webpush(**kwargs):
        raise exc

    monkeypatch.setattr("app.core.web_push.webpush", fake_webpush)
    result = send_push_for_notification(session, user_id="dev-local", row=row)
    session.commit()

    assert result["failed"] == 1
    sub = session.exec(
        select(PushSubscription).where(PushSubscription.endpoint == "https://push.example/stale")
    ).first()
    assert sub is not None
    assert sub.enabled is False
    attempts = list(session.exec(select(NotificationDeliveryAttempt)).all())
    assert any(a.status == "stale_subscription" for a in attempts)


def test_no_duplicate_push_within_repeat_window(session, vapid, monkeypatch):
    session.add(
        Bill(
            name="Electric",
            amount=120,
            required=True,
            due_date=date.today(),
            recurrence="monthly",
        )
    )
    session.commit()

    calls = 0

    def fake_webpush(**kwargs):
        nonlocal calls
        calls += 1

    monkeypatch.setattr("app.core.web_push.webpush", fake_webpush)
    upsert_subscription(
        session,
        user_id="dev-local",
        endpoint="https://push.example/dup",
        p256dh_key="p256",
        auth_key="auth",
    )
    session.commit()

    sync_notifications(session, user_id="dev-local")
    sync_notifications(session, user_id="dev-local")
    assert calls == 1


def test_api_subscribe_and_public_key(client, vapid):
    res = client.get("/api/push/public-key")
    assert res.status_code == 200
    assert res.json()["public_key"]

    sub = client.post(
        "/api/push/subscribe",
        json={
            "endpoint": "https://push.example/api",
            "keys": {"p256dh": "abc", "auth": "def"},
        },
    )
    assert sub.status_code == 200
    assert sub.json()["enabled"] is True

    test = client.post("/api/push/test")
    assert test.status_code == 200
    assert test.json()["sent"] >= 0


def test_unsubscribe_disables_subscription(session):
    upsert_subscription(
        session,
        user_id="dev-local",
        endpoint="https://push.example/off",
        p256dh_key="p256",
        auth_key="auth",
    )
    session.commit()
    ok = disable_subscription(session, "https://push.example/off")
    session.commit()
    assert ok is True
    sub = session.exec(
        select(PushSubscription).where(PushSubscription.endpoint == "https://push.example/off")
    ).first()
    assert sub is not None
    assert sub.enabled is False
