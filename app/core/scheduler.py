"""Local job scheduler — runs CFO maintenance on cadence."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from sqlmodel import Session, select

from app.core.backup_restore import create_backup
from app.core.data_quality import assess_data_quality
from app.core.financial_timeline import maybe_capture_all_cadences
from app.core.notifications import sync_notifications
from app.core.stress_forecast import run_stress_forecast
from app.core.v2_intelligence import run_v2_intelligence_refresh
from app.db.models import ScheduledJob
from app.events.bus import event_bus
from app.events.types import Event, EventType

JOB_TYPES = frozenset({
    "notification_sync",
    "stress_forecast",
    "backup_reminder",
    "snapshot_capture",
    "data_quality_check",
    "opportunity_refresh",
})
LEGACY_RETIRED_JOBS = frozenset({"daily_briefing", "cfo_report", "research_ingestion_check"})

CADENCES = frozenset({"hourly", "daily", "weekly"})
RUN_STALE_MINUTES = 120

_active_lock = threading.Lock()
_active_jobs: set[str] = set()

DEFAULT_JOBS: tuple[dict[str, Any], ...] = (
    {"job_type": "notification_sync", "cadence": "hourly", "enabled": True, "config": {}},
    {"job_type": "stress_forecast", "cadence": "daily", "enabled": True, "config": {}},
    {
        "job_type": "backup_reminder",
        "cadence": "weekly",
        "enabled": False,
        "config": {"create_backup": False},
    },
    {"job_type": "snapshot_capture", "cadence": "daily", "enabled": True, "config": {}},
    {"job_type": "data_quality_check", "cadence": "daily", "enabled": True, "config": {}},
    {"job_type": "opportunity_refresh", "cadence": "daily", "enabled": True, "config": {}},
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _cadence_delta(cadence: str) -> timedelta:
    if cadence == "hourly":
        return timedelta(hours=1)
    if cadence == "weekly":
        return timedelta(days=7)
    return timedelta(days=1)


def compute_next_run(from_time: datetime, cadence: str) -> datetime:
    return from_time + _cadence_delta(cadence)


def job_to_dict(row: ScheduledJob) -> dict[str, Any]:
    try:
        config = json.loads(row.config_json or "{}")
    except json.JSONDecodeError:
        config = {}
    return {
        "id": row.id,
        "job_type": row.job_type,
        "enabled": row.enabled,
        "cadence": row.cadence,
        "last_run_at": row.last_run_at.isoformat() if row.last_run_at else None,
        "next_run_at": row.next_run_at.isoformat() if row.next_run_at else None,
        "last_status": row.last_status,
        "last_error": row.last_error,
        "config": config,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def ensure_default_jobs(session: Session) -> list[ScheduledJob]:
    existing = {j.job_type: j for j in session.exec(select(ScheduledJob)).all()}
    created: list[ScheduledJob] = []
    bumped: list[ScheduledJob] = []
    now = _now()
    for spec in DEFAULT_JOBS:
        jt = spec["job_type"]
        if jt in existing:
            if jt == "notification_sync" and existing[jt].cadence == "daily":
                existing[jt].cadence = "hourly"
                existing[jt].updated_at = now
                session.add(existing[jt])
                bumped.append(existing[jt])
            continue
        row = ScheduledJob(
            job_type=jt,
            enabled=bool(spec.get("enabled", True)),
            cadence=spec.get("cadence", "daily"),
            next_run_at=now,
            config_json=json.dumps(spec.get("config") or {}),
        )
        session.add(row)
        created.append(row)
    # Preserve historical scheduler rows while making retired advice jobs
    # permanently inert.  Their records remain readable for audit/backup.
    for jt in LEGACY_RETIRED_JOBS:
        row = existing.get(jt)
        if row is not None and (row.enabled or _parse_config(row).get("runtime") != "legacy_advice"):
            config = _parse_config(row)
            config.update({"deprecated": True, "runtime": "legacy_advice"})
            row.enabled = False
            row.config_json = json.dumps(config, sort_keys=True)
            row.updated_at = now
            session.add(row)
            bumped.append(row)
    if created or bumped:
        session.commit()
        for row in created:
            session.refresh(row)
    return list(session.exec(select(ScheduledJob).order_by(ScheduledJob.job_type)).all())  # type: ignore[arg-type]


def disable_legacy_advice_jobs(session: Session) -> list[dict[str, Any]]:
    """Explicitly retire scheduled V1 advice while preserving its history.

    This is an operator/migration action, not an automatic override of a
    user's scheduler settings.  It is called by the D012 deployment procedure
    after the current rows have been inspected and backed up.
    """

    rows = list(
        session.exec(
            select(ScheduledJob).where(ScheduledJob.job_type.in_(list(LEGACY_RETIRED_JOBS)))
        ).all()
    )
    now = _now()
    for row in rows:
        config = _parse_config(row)
        config.update({"deprecated": True, "runtime": "legacy_advice"})
        row.enabled = False
        row.config_json = json.dumps(config, sort_keys=True)
        row.updated_at = now
        session.add(row)
    if rows:
        session.commit()
    return [job_to_dict(row) for row in rows]


def list_jobs(session: Session) -> list[dict[str, Any]]:
    rows = ensure_default_jobs(session)
    return [job_to_dict(r) for r in rows]


def update_job(
    session: Session,
    job_id: str,
    *,
    enabled: bool | None = None,
    cadence: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = session.get(ScheduledJob, job_id)
    if not row:
        raise ValueError("Job not found")
    if enabled is not None:
        row.enabled = enabled
    if cadence is not None:
        if cadence not in CADENCES:
            raise ValueError(f"cadence must be one of {sorted(CADENCES)}")
        row.cadence = cadence
    if config is not None:
        row.config_json = json.dumps(config)
    row.updated_at = _now()
    if row.next_run_at is None:
        row.next_run_at = _now()
    session.add(row)
    session.commit()
    session.refresh(row)
    return job_to_dict(row)


def _parse_config(row: ScheduledJob) -> dict[str, Any]:
    try:
        return json.loads(row.config_json or "{}")
    except json.JSONDecodeError:
        return {}


def _is_stale_running(row: ScheduledJob) -> bool:
    if row.last_status != "running" or not row.last_run_at:
        return False
    return (_now() - row.last_run_at) > timedelta(minutes=RUN_STALE_MINUTES)


def _try_acquire(job_type: str) -> bool:
    with _active_lock:
        if job_type in _active_jobs:
            return False
        _active_jobs.add(job_type)
        return True


def _release(job_type: str) -> None:
    with _active_lock:
        _active_jobs.discard(job_type)


def _run_notification_sync(session: Session, user_id: str, _config: dict[str, Any]) -> dict[str, Any]:
    return sync_notifications(session, user_id=user_id)


def _run_stress_forecast(session: Session, _user_id: str, _config: dict[str, Any]) -> dict[str, Any]:
    forecast = run_stress_forecast(session, horizon_days=90)
    return {"status": forecast.get("status"), "pressure_score": forecast.get("pressure_score")}


def _run_backup_reminder(session: Session, user_id: str, config: dict[str, Any]) -> dict[str, Any]:
    if config.get("create_backup"):
        return create_backup(session, user_id, include_evidence=False)
    sync_notifications(session, user_id=user_id)
    return {"reminder_only": True}


def _run_snapshot_capture(session: Session, _user_id: str, _config: dict[str, Any]) -> dict[str, Any]:
    stored = maybe_capture_all_cadences(session)
    return {"captured": len(stored)}


def _run_data_quality(session: Session, _user_id: str, _config: dict[str, Any]) -> dict[str, Any]:
    report = assess_data_quality(session, emit_events=True)
    return {"status": report.get("status"), "overall_score": report.get("overall_score")}


def _run_opportunity_refresh(session: Session, user_id: str, _config: dict[str, Any]) -> dict[str, Any]:
    result = run_v2_intelligence_refresh(session, trigger="scheduler")
    result["user_id"] = user_id
    return result


JOB_RUNNERS: dict[str, Callable[..., dict[str, Any]]] = {
    "notification_sync": _run_notification_sync,
    "stress_forecast": _run_stress_forecast,
    "backup_reminder": _run_backup_reminder,
    "snapshot_capture": _run_snapshot_capture,
    "data_quality_check": _run_data_quality,
    "opportunity_refresh": _run_opportunity_refresh,
}


def is_job_due(row: ScheduledJob, *, now: datetime | None = None) -> bool:
    ref = now or _now()
    if not row.enabled:
        return False
    if row.last_status == "running" and not _is_stale_running(row):
        return False
    if row.next_run_at is None:
        return True
    return row.next_run_at <= ref


def run_job(
    session: Session,
    job_id: str,
    *,
    user_id: str,
    force: bool = False,
) -> dict[str, Any]:
    row = session.get(ScheduledJob, job_id)
    if not row:
        raise ValueError("Job not found")
    if not row.enabled and not force:
        return {"ok": False, "skipped": "disabled", "job": job_to_dict(row)}
    return _execute_job(session, row, user_id=user_id, force=force)


def _execute_job(
    session: Session,
    row: ScheduledJob,
    *,
    user_id: str,
    force: bool,
) -> dict[str, Any]:
    if row.job_type not in JOB_RUNNERS:
        if row.job_type in LEGACY_RETIRED_JOBS:
            return {"ok": False, "skipped": "retired", "job": job_to_dict(row)}
        raise ValueError(f"Unknown job type: {row.job_type}")

    if row.last_status == "running" and not _is_stale_running(row) and not force:
        event_bus.emit(
            Event(
                event_type=EventType.SCHEDULER_JOB_SKIPPED_OVERLAP.value,
                source="scheduler",
                payload={"job_id": row.id, "job_type": row.job_type},
            ),
            session=session,
        )
        return {"ok": False, "skipped": "overlap", "job": job_to_dict(row)}

    if not _try_acquire(row.job_type):
        event_bus.emit(
            Event(
                event_type=EventType.SCHEDULER_JOB_SKIPPED_OVERLAP.value,
                source="scheduler",
                payload={"job_id": row.id, "job_type": row.job_type},
            ),
            session=session,
        )
        return {"ok": False, "skipped": "overlap_in_process", "job": job_to_dict(row)}

    now = _now()
    row.last_status = "running"
    row.last_run_at = now
    row.last_error = ""
    row.updated_at = now
    session.add(row)
    session.commit()

    event_bus.emit(
        Event(
            event_type=EventType.SCHEDULER_JOB_STARTED.value,
            source="scheduler",
            payload={"job_id": row.id, "job_type": row.job_type},
        ),
        session=session,
    )

    try:
        runner = JOB_RUNNERS[row.job_type]
        result = runner(session, user_id, _parse_config(row))
        row.last_status = "success"
        row.last_error = ""
        row.next_run_at = compute_next_run(_now(), row.cadence)
        event_bus.emit(
            Event(
                event_type=EventType.SCHEDULER_JOB_COMPLETED.value,
                source="scheduler",
                payload={"job_id": row.id, "job_type": row.job_type, "result": result},
            ),
            session=session,
        )
        out = {"ok": True, "result": result, "job": job_to_dict(row)}
    except Exception as exc:
        row.last_status = "failed"
        row.last_error = str(exc)[:500]
        row.next_run_at = compute_next_run(_now(), row.cadence)
        event_bus.emit(
            Event(
                event_type=EventType.SCHEDULER_JOB_FAILED.value,
                source="scheduler",
                severity="error",
                payload={"job_id": row.id, "job_type": row.job_type, "error": str(exc)},
            ),
            session=session,
        )
        out = {"ok": False, "error": str(exc), "job": job_to_dict(row)}
    finally:
        row.updated_at = _now()
        session.add(row)
        session.commit()
        session.refresh(row)
        out["job"] = job_to_dict(row)
        _release(row.job_type)

    return out


def run_due_jobs(session: Session, *, user_id: str) -> dict[str, Any]:
    ensure_default_jobs(session)
    now = _now()
    results: list[dict[str, Any]] = []
    for row in session.exec(select(ScheduledJob)).all():
        if not is_job_due(row, now=now):
            continue
        results.append(run_job(session, row.id, user_id=user_id))
    return {"ran": len(results), "results": results}
