"""Web Push delivery for PWA background notifications (VAPID)."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from py_vapid.utils import b64urlencode
from pywebpush import WebPushException, webpush
from sqlmodel import Session, select

from app.config import settings
from app.db.models import NotificationDeliveryAttempt, NotificationInbox, PushSubscription

logger = logging.getLogger(__name__)

PUSH_BILL_TYPES = frozenset({"bill_due_soon"})
STALE_SUBSCRIPTION_STATUSES = frozenset({404, 410})
PUSH_REPEAT_HOURS = 4


def export_vapid_keypair() -> tuple[str, str]:
    """Generate VAPID keys: (public_key_b64url, private_key_pem)."""
    from py_vapid import Vapid

    vapid = Vapid()
    vapid.generate_keys()
    public_raw = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    public_b64url = b64urlencode(public_raw)
    private_pem = vapid.private_key.private_bytes(
        Encoding.PEM,
        PrivateFormat.PKCS8,
        NoEncryption(),
    ).decode("ascii")
    return public_b64url, private_pem


def _normalize_vapid_private_key(raw: str) -> str:
    return raw.replace("\\n", "\n").strip()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def vapid_configured() -> bool:
    pub = settings.vapid_public_key
    priv = settings.vapid_private_key
    return bool(str(pub).strip() and str(priv).strip())


def public_vapid_key() -> str:
    return str(settings.vapid_public_key).strip()


def _public_base_prefix() -> str:
    raw = os.getenv("LEDGERMIND_PUBLIC_BASE", "").strip()
    if raw in ("", "/"):
        return ""
    return f"/{raw.strip('/')}"


def notification_open_url(link_path: str) -> str:
    """Absolute app path for service worker navigation (includes /lm when configured)."""
    path = link_path if link_path.startswith("/") else f"/{link_path}"
    prefix = _public_base_prefix()
    if prefix and not path.startswith(prefix):
        return f"{prefix}{path}"
    return path


def push_tag_for_notification(row: NotificationInbox) -> str:
    return row.dedupe_key.replace(":", "-")


def push_payload_for_notification(row: NotificationInbox) -> dict[str, Any]:
    url = notification_open_url(row.link_path or "/notifications")
    require_interaction = row.severity == "critical"
    return {
        "title": row.title,
        "body": row.body,
        "url": url,
        "tag": push_tag_for_notification(row),
        "notificationId": row.id,
        "requireInteraction": require_interaction,
    }


def subscription_to_dict(row: PushSubscription) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "endpoint": row.endpoint,
        "device_label": row.device_label,
        "enabled": row.enabled,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
        "last_failure_at": row.last_failure_at.isoformat() if row.last_failure_at else None,
        "failure_count": row.failure_count,
    }


def upsert_subscription(
    session: Session,
    *,
    user_id: str,
    endpoint: str,
    p256dh_key: str,
    auth_key: str,
    user_agent: str = "",
    device_label: str = "",
) -> PushSubscription:
    rows = list(
        session.exec(
            select(PushSubscription).where(PushSubscription.endpoint == endpoint)
        ).all()
    )
    now = _now()
    if rows:
        row = rows[0]
        row.user_id = user_id
        row.p256dh_key = p256dh_key
        row.auth_key = auth_key
        row.user_agent = user_agent or row.user_agent
        row.device_label = device_label or row.device_label
        row.enabled = True
        row.updated_at = now
        row.failure_count = 0
        session.add(row)
        return row

    row = PushSubscription(
        user_id=user_id,
        endpoint=endpoint,
        p256dh_key=p256dh_key,
        auth_key=auth_key,
        user_agent=user_agent,
        device_label=device_label,
    )
    session.add(row)
    session.flush()
    return row


def disable_subscription(session: Session, endpoint: str) -> bool:
    rows = list(
        session.exec(select(PushSubscription).where(PushSubscription.endpoint == endpoint)).all()
    )
    if not rows:
        return False
    now = _now()
    for row in rows:
        row.enabled = False
        row.updated_at = now
        session.add(row)
    return True


def list_enabled_subscriptions(session: Session, user_id: str) -> list[PushSubscription]:
    return list(
        session.exec(
            select(PushSubscription)
            .where(PushSubscription.user_id == user_id)
            .where(PushSubscription.enabled == True)  # noqa: E712
        ).all()
    )


def _last_successful_push(session: Session, notification_id: str) -> datetime | None:
    rows = list(
        session.exec(
            select(NotificationDeliveryAttempt)
            .where(NotificationDeliveryAttempt.notification_id == notification_id)
            .where(NotificationDeliveryAttempt.channel == "web_push")
            .where(NotificationDeliveryAttempt.status == "success")
            .order_by(NotificationDeliveryAttempt.attempted_at.desc())  # type: ignore[arg-type]
        ).all()
    )
    if not rows:
        return None
    return rows[0].attempted_at


def should_send_web_push(
    session: Session,
    row: NotificationInbox,
    *,
    was_created: bool,
    escalated_now: bool,
) -> bool:
    if row.notification_type not in PUSH_BILL_TYPES:
        return False
    if row.status != "active":
        return False
    if was_created or escalated_now:
        return True
    if row.severity != "critical":
        return False
    try:
        payload = json.loads(row.payload_json or "{}")
        due_str = str(payload.get("due_date", ""))[:10]
        if not due_str:
            return False
        from datetime import date

        if date.fromisoformat(due_str) != date.today():
            return False
    except ValueError:
        return False
    last = _last_successful_push(session, row.id)
    if last is None:
        return True
    return (_now() - last) >= timedelta(hours=PUSH_REPEAT_HOURS)


def _record_attempt(
    session: Session,
    *,
    notification_id: str,
    push_subscription_id: str,
    status: str,
    provider_status_code: int = 0,
    error_message: str = "",
) -> NotificationDeliveryAttempt:
    attempt = NotificationDeliveryAttempt(
        notification_id=notification_id,
        push_subscription_id=push_subscription_id,
        channel="web_push",
        status=status,
        provider_status_code=provider_status_code,
        error_message=error_message[:500],
    )
    session.add(attempt)
    return attempt


def _send_to_subscription(
    session: Session,
    sub: PushSubscription,
    payload: dict[str, Any],
    *,
    notification_id: str,
) -> bool:
    if not vapid_configured():
        _record_attempt(
            session,
            notification_id=notification_id,
            push_subscription_id=sub.id,
            status="failed",
            error_message="VAPID not configured",
        )
        return False

    subscription_info = {
        "endpoint": sub.endpoint,
        "keys": {"p256dh": sub.p256dh_key, "auth": sub.auth_key},
    }
    now = _now()
    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload),
            vapid_private_key=_normalize_vapid_private_key(str(settings.vapid_private_key)),
            vapid_claims={"sub": settings.vapid_subject},
        )
        sub.last_success_at = now
        sub.last_failure_at = None
        sub.failure_count = 0
        sub.updated_at = now
        session.add(sub)
        _record_attempt(
            session,
            notification_id=notification_id,
            push_subscription_id=sub.id,
            status="success",
        )
        return True
    except WebPushException as exc:
        status_code = int(exc.response.status_code) if exc.response is not None else 0
        err = str(exc)
        if status_code in STALE_SUBSCRIPTION_STATUSES:
            sub.enabled = False
            sub.last_failure_at = now
            sub.updated_at = now
            session.add(sub)
            _record_attempt(
                session,
                notification_id=notification_id,
                push_subscription_id=sub.id,
                status="stale_subscription",
                provider_status_code=status_code,
                error_message=err,
            )
        else:
            sub.last_failure_at = now
            sub.failure_count = (sub.failure_count or 0) + 1
            sub.updated_at = now
            session.add(sub)
            _record_attempt(
                session,
                notification_id=notification_id,
                push_subscription_id=sub.id,
                status="failed",
                provider_status_code=status_code,
                error_message=err,
            )
        logger.warning("Web push failed for subscription %s: %s", sub.id, err)
        return False
    except Exception as exc:
        sub.last_failure_at = now
        sub.failure_count = (sub.failure_count or 0) + 1
        sub.updated_at = now
        session.add(sub)
        _record_attempt(
            session,
            notification_id=notification_id,
            push_subscription_id=sub.id,
            status="failed",
            error_message=str(exc),
        )
        logger.warning("Web push error for subscription %s: %s", sub.id, exc)
        return False


def send_push_payload(
    session: Session,
    *,
    user_id: str,
    payload: dict[str, Any],
    notification_id: str = "",
) -> dict[str, Any]:
    subs = list_enabled_subscriptions(session, user_id)
    if not subs:
        return {"sent": 0, "failed": 0, "skipped": "no_subscriptions"}
    if not vapid_configured():
        return {"sent": 0, "failed": 0, "skipped": "vapid_not_configured"}

    sent = 0
    failed = 0
    for sub in subs:
        ok = _send_to_subscription(session, sub, payload, notification_id=notification_id)
        if ok:
            sent += 1
        else:
            failed += 1
    return {"sent": sent, "failed": failed}


def send_push_for_notification(
    session: Session,
    *,
    user_id: str,
    row: NotificationInbox,
) -> dict[str, Any]:
    payload = push_payload_for_notification(row)
    return send_push_payload(session, user_id=user_id, payload=payload, notification_id=row.id)


def send_test_push(session: Session, *, user_id: str) -> dict[str, Any]:
    payload = {
        "title": "Ledger test",
        "body": "Web Push is working on this device.",
        "url": notification_open_url("/notifications"),
        "tag": "ledgermind-test",
        "notificationId": "",
        "requireInteraction": False,
    }
    return send_push_payload(session, user_id=user_id, payload=payload, notification_id="")
