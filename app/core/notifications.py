"""Dashboard notification inbox — advisory alerts from household signals."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from app.core.backup_restore import list_backups
from app.core.cashflow_engine import calculate_cashflow
from app.core.data_quality import assess_data_quality
from app.db.models import (
    FinancialAction,
    NotificationInbox,
)
from app.events.bus import event_bus
from app.events.types import Event, EventType

NOTIFICATION_TYPES = frozenset({
    "bill_due_soon",
    "cashflow_danger",
    "action_overdue",
    "data_quality_blocking",
    "backup_due",
})

CHANNEL_INBOX = "dashboard_inbox"
SEVERITIES = frozenset({"info", "warning", "critical"})
BACKUP_REMINDER_DAYS = 30
BILL_DUE_WINDOW_DAYS = 7
WEEK_DAYS = 7


@dataclass(frozen=True)
class NotificationSpec:
    notification_type: str
    severity: str
    title: str
    body: str
    link_path: str
    dedupe_key: str
    entity_type: str = ""
    entity_id: str = ""
    payload: dict[str, Any] | None = None


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def notification_to_dict(row: NotificationInbox) -> dict[str, Any]:
    try:
        payload = json.loads(row.payload_json or "{}")
    except json.JSONDecodeError:
        payload = {}
    return {
        "id": row.id,
        "notification_type": row.notification_type,
        "channel": row.channel,
        "severity": row.severity,
        "title": row.title,
        "body": row.body,
        "link_path": row.link_path,
        "dedupe_key": row.dedupe_key,
        "status": row.status,
        "snoozed_until": row.snoozed_until.isoformat() if row.snoozed_until else None,
        "dismiss_reason": row.dismiss_reason,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "payload": payload,
        "escalated": row.escalated,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _existing_for_dedupe(session: Session, dedupe_key: str) -> NotificationInbox | None:
    rows = list(
        session.exec(
            select(NotificationInbox)
            .where(NotificationInbox.dedupe_key == dedupe_key)
            .order_by(NotificationInbox.updated_at.desc())  # type: ignore[arg-type]
        ).all()
    )
    return rows[0] if rows else None


def upsert_notification(session: Session, spec: NotificationSpec) -> tuple[NotificationInbox, bool, bool]:
    """Insert or refresh an inbox row. Returns (row, created, escalated_now)."""
    if spec.notification_type not in NOTIFICATION_TYPES:
        raise ValueError(f"Unknown notification type: {spec.notification_type}")
    if spec.severity not in SEVERITIES:
        raise ValueError(f"Invalid severity: {spec.severity}")

    existing = _existing_for_dedupe(session, spec.dedupe_key)
    payload_json = json.dumps(spec.payload or {}, default=str)
    now = _now()
    escalated_now = False

    if existing:
        if existing.status == "dismissed":
            return existing, False, False
        if existing.status == "snoozed" and existing.snoozed_until and existing.snoozed_until > now:
            return existing, False, False
        if existing.status == "snoozed" and existing.snoozed_until and existing.snoozed_until <= now:
            existing.status = "active"
            existing.snoozed_until = None
            existing.escalated = True
            existing.updated_at = now
            escalated_now = True
            event_bus.emit(
                Event(
                    event_type=EventType.NOTIFICATION_ESCALATED.value,
                    source="notifications",
                    payload={"notification_id": existing.id, "dedupe_key": spec.dedupe_key},
                ),
                session=session,
            )
        existing.title = spec.title
        existing.body = spec.body
        existing.severity = spec.severity
        existing.link_path = spec.link_path
        existing.payload_json = payload_json
        existing.updated_at = now
        session.add(existing)
        return existing, False, escalated_now

    row = NotificationInbox(
        notification_type=spec.notification_type,
        channel=CHANNEL_INBOX,
        severity=spec.severity,
        title=spec.title,
        body=spec.body,
        link_path=spec.link_path,
        dedupe_key=spec.dedupe_key,
        status="active",
        entity_type=spec.entity_type,
        entity_id=spec.entity_id,
        payload_json=payload_json,
    )
    session.add(row)
    session.flush()
    event_bus.emit(
        Event(
            event_type=EventType.NOTIFICATION_CREATED.value,
            source="notifications",
            severity=spec.severity,
            payload={
                "notification_id": row.id,
                "notification_type": spec.notification_type,
                "dedupe_key": spec.dedupe_key,
            },
        ),
        session=session,
    )
    return row, True, False


def scan_bill_due_soon(session: Session) -> list[NotificationSpec]:
    from app.core.obligation_schedule import obligation_events_in_range

    today = date.today()
    end = today + timedelta(days=BILL_DUE_WINDOW_DAYS)
    events = obligation_events_in_range(session, today, end)
    specs: list[NotificationSpec] = []
    for ev in events:
        due_str = str(ev.get("due_date", ""))[:10]
        if not due_str:
            continue
        due = date.fromisoformat(due_str)
        name = ev.get("name", "Payment")
        amount = float(ev.get("amount") or 0)
        kind = str(ev.get("kind") or "bill")
        days_until = (due - today).days
        if days_until == 0:
            title = f"Due today: {name}"
            severity = "critical"
        elif days_until == 1:
            title = f"Due tomorrow: {name}"
            severity = "warning"
        else:
            title = f"Due soon: {name}"
            severity = "warning" if ev.get("required", True) else "info"
        link = "/money"
        if kind == "credit_card":
            link = "/credit-cards"
        elif kind in ("mortgage", "rent"):
            link = "/mortgage"
        elif kind == "debt":
            link = "/debts"
        specs.append(
            NotificationSpec(
                notification_type="bill_due_soon",
                severity=severity,
                title=title,
                body=f"${amount:,.2f} due {due_str} ({kind.replace('_', ' ')})",
                link_path=link,
                dedupe_key=f"bill_due_soon:{kind}:{name}:{due_str}",
                entity_type=kind,
                payload={"due_date": due_str, "amount": amount, "kind": kind},
            )
        )
    return specs


def scan_cashflow_danger(session: Session) -> list[NotificationSpec]:
    cf = calculate_cashflow(session)
    risk = cf.get("risk_level")
    if risk not in ("danger", "tight"):
        return []
    severity = "critical" if risk == "danger" else "warning"
    warning = (cf.get("warnings") or ["Cash runway is tight"])[0]
    return [
        NotificationSpec(
            notification_type="cashflow_danger",
            severity=severity,
            title="Cashflow needs attention",
            body=warning,
            link_path="/cashflow",
            dedupe_key=f"cashflow_danger:{risk}",
            payload={"risk_level": risk, "safe_to_spend": cf.get("safe_to_spend")},
        )
    ]


def scan_action_overdue(session: Session) -> list[NotificationSpec]:
    today = date.today()
    specs: list[NotificationSpec] = []
    for act in session.exec(select(FinancialAction)).all():
        if act.status not in ("open", "in_progress"):
            continue
        if not act.due_date or act.due_date >= today:
            continue
        specs.append(
            NotificationSpec(
                notification_type="action_overdue",
                severity="critical" if act.is_urgent else "warning",
                title=f"Overdue action: {act.title}",
                body=f"Was due {act.due_date.isoformat()}",
                link_path="/moves",
                dedupe_key=f"action_overdue:{act.id}",
                entity_type="financial_action",
                entity_id=act.id,
                payload={"due_date": act.due_date.isoformat()},
            )
        )
    return specs


def scan_data_quality_blocking(session: Session) -> list[NotificationSpec]:
    report = assess_data_quality(session, emit_events=False)
    blocking = report.get("blocking_issues") or []
    if not blocking:
        return []
    first = blocking[0]
    return [
        NotificationSpec(
            notification_type="data_quality_blocking",
            severity="critical",
            title="Data quality blocking advice",
            body=str(first.get("message", "Fix blocking data issues before trusting rate-sensitive advice")),
            link_path="/data-quality",
            dedupe_key="data_quality_blocking:aggregate",
            payload={"blocking_count": len(blocking), "status": report.get("status")},
        )
    ]


def scan_backup_due(user_id: str) -> list[NotificationSpec]:
    backups = list_backups(user_id)
    if not backups:
        return [
            NotificationSpec(
                notification_type="backup_due",
                severity="warning",
                title="No household backup yet",
                body="Create a backup to protect your financial data.",
                link_path="/backup",
                dedupe_key="backup_due:none",
            )
        ]
    latest = backups[0].get("created_at", "")
    try:
        created = datetime.fromisoformat(latest.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        created = _now() - timedelta(days=BACKUP_REMINDER_DAYS + 1)
    if (_now() - created).days < BACKUP_REMINDER_DAYS:
        return []
    return [
        NotificationSpec(
            notification_type="backup_due",
            severity="info",
            title="Backup reminder",
            body=f"Last backup: {latest[:10]}. Consider creating a fresh export.",
            link_path="/backup",
            dedupe_key="backup_due:reminder",
            payload={"last_backup_at": latest},
        )
    ]


def collect_notification_specs(session: Session, *, user_id: str) -> list[NotificationSpec]:
    specs: list[NotificationSpec] = []
    specs.extend(scan_bill_due_soon(session))
    specs.extend(scan_cashflow_danger(session))
    specs.extend(scan_action_overdue(session))
    specs.extend(scan_data_quality_blocking(session))
    specs.extend(scan_backup_due(user_id))
    return specs


def sync_notifications(session: Session, *, user_id: str) -> dict[str, Any]:
    """Refresh inbox from current household signals; send Web Push for bill alerts."""
    from app.core.web_push import send_push_for_notification, should_send_web_push

    created = 0
    updated = 0
    pushed = 0
    push_failures = 0
    push_queue: list[NotificationInbox] = []

    for spec in collect_notification_specs(session, user_id=user_id):
        row, was_created, escalated_now = upsert_notification(session, spec)
        if was_created:
            created += 1
        else:
            updated += 1
        if should_send_web_push(
            session, row, was_created=was_created, escalated_now=escalated_now
        ):
            push_queue.append(row)

    session.commit()

    for row in push_queue:
        result = send_push_for_notification(session, user_id=user_id, row=row)
        pushed += int(result.get("sent", 0))
        push_failures += int(result.get("failed", 0))

    if push_queue:
        session.commit()

    return {
        "created": created,
        "refreshed": updated,
        "pushed": pushed,
        "push_failures": push_failures,
    }


def list_inbox(session: Session, *, include_dismissed: bool = False) -> dict[str, Any]:
    rows = list(session.exec(select(NotificationInbox).order_by(NotificationInbox.created_at.desc())).all())  # type: ignore[arg-type]
    now = _now()
    active: list[dict[str, Any]] = []
    snoozed: list[dict[str, Any]] = []
    dismissed: list[dict[str, Any]] = []
    critical: list[dict[str, Any]] = []
    due_this_week: list[dict[str, Any]] = []
    week_end = date.today() + timedelta(days=WEEK_DAYS)

    for row in rows:
        d = notification_to_dict(row)
        if row.status == "dismissed":
            if include_dismissed:
                dismissed.append(d)
            continue
        if row.status == "snoozed":
            if row.snoozed_until and row.snoozed_until > now:
                snoozed.append(d)
                continue
            row.status = "active"
            row.snoozed_until = None
            session.add(row)
        active.append(d)
        if row.severity == "critical":
            critical.append(d)
        payload = d.get("payload") or {}
        due = payload.get("due_date")
        if due:
            try:
                due_d = date.fromisoformat(str(due)[:10])
                if date.today() <= due_d <= week_end:
                    due_this_week.append(d)
            except ValueError:
                pass
        elif row.notification_type in ("action_overdue", "bill_due_soon"):
            due_this_week.append(d)

    session.commit()
    return {
        "critical": critical,
        "due_this_week": due_this_week,
        "active": active,
        "snoozed": snoozed,
        "dismissed": dismissed,
        "counts": {
            "critical": len(critical),
            "active": len(active),
            "snoozed": len(snoozed),
            "dismissed": len(dismissed),
        },
    }


def dismiss_notification(
    session: Session,
    notification_id: str,
    *,
    reason: str = "",
) -> dict[str, Any]:
    row = session.get(NotificationInbox, notification_id)
    if not row:
        raise ValueError("Notification not found")
    if row.severity == "critical" and not (reason or "").strip():
        raise ValueError("Critical alerts require a dismiss reason")
    row.status = "dismissed"
    row.dismiss_reason = reason.strip()
    row.updated_at = _now()
    session.add(row)
    session.commit()
    event_bus.emit(
        Event(
            event_type=EventType.NOTIFICATION_DISMISSED.value,
            source="notifications",
            payload={"notification_id": row.id, "reason": row.dismiss_reason},
        ),
        session=session,
    )
    return notification_to_dict(row)


def snooze_notification(
    session: Session,
    notification_id: str,
    *,
    days: int = 1,
) -> dict[str, Any]:
    if days < 1 or days > 30:
        raise ValueError("Snooze days must be 1–30")
    row = session.get(NotificationInbox, notification_id)
    if not row:
        raise ValueError("Notification not found")
    row.status = "snoozed"
    row.snoozed_until = _now() + timedelta(days=days)
    row.updated_at = _now()
    session.add(row)
    session.commit()
    event_bus.emit(
        Event(
            event_type=EventType.NOTIFICATION_SNOOZED.value,
            source="notifications",
            payload={"notification_id": row.id, "days": days},
        ),
        session=session,
    )
    return notification_to_dict(row)
