"""Local scheduler tests."""

import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.scheduler import (
    _now,
    compute_next_run,
    disable_legacy_advice_jobs,
    ensure_default_jobs,
    is_job_due,
    run_due_jobs,
    run_job,
    update_job,
)
from app.db.models import ScheduledJob
from app.main import app


@pytest.fixture
def client():
    from app.db.session import init_db

    init_db()
    return TestClient(app)


def test_ensure_default_jobs_seeds_all_types(session):
    rows = ensure_default_jobs(session)
    types = {r.job_type for r in rows}
    assert "daily_briefing" not in types
    assert "cfo_report" not in types
    assert "notification_sync" in types
    assert len(types) >= 6


def test_legacy_advice_migration_is_explicit_and_preserves_history(session):
    daily = ScheduledJob(job_type="daily_briefing", enabled=True, last_status="success")
    report = ScheduledJob(job_type="cfo_report", enabled=True, last_status="success")
    session.add(daily)
    session.add(report)
    session.commit()

    changed = disable_legacy_advice_jobs(session)

    assert {row["job_type"] for row in changed} == {"daily_briefing", "cfo_report"}
    assert all(row["enabled"] is False for row in changed)
    assert session.get(ScheduledJob, daily.id).last_status == "success"
    assert session.get(ScheduledJob, report.id).last_status == "success"


def test_disabled_job_skipped(session):
    rows = ensure_default_jobs(session)
    job = next(r for r in rows if r.job_type == "backup_reminder")
    update_job(session, job.id, enabled=False)
    out = run_job(session, job.id, user_id="dev-local")
    assert out.get("skipped") == "disabled"


def test_manual_run_works(session):
    rows = ensure_default_jobs(session)
    job = next(r for r in rows if r.job_type == "stress_forecast")

    def fake_forecast(sess, _uid, _cfg):
        return {"status": "safe", "pressure_score": 10}

    with patch.dict(
        "app.core.scheduler.JOB_RUNNERS",
        {"stress_forecast": fake_forecast},
    ):
        out = run_job(session, job.id, user_id="dev-local", force=True)

    assert out["ok"] is True
    assert out["job"]["last_status"] == "success"


def test_overlap_prevented(session):
    rows = ensure_default_jobs(session)
    job = next(r for r in rows if r.job_type == "data_quality_check")
    job.last_status = "running"
    job.last_run_at = _now()
    session.add(job)
    session.commit()
    out = run_job(session, job.id, user_id="dev-local")
    assert out.get("skipped") == "overlap"


def test_failed_job_records_error(session):
    rows = ensure_default_jobs(session)
    job = next(r for r in rows if r.job_type == "notification_sync")

    def boom(_s, _u, _c):
        raise RuntimeError("sync failed")

    with patch.dict("app.core.scheduler.JOB_RUNNERS", {"notification_sync": boom}):
        out = run_job(session, job.id, user_id="dev-local", force=True)

    assert out["ok"] is False
    assert "sync failed" in out.get("error", "")
    refreshed = session.get(ScheduledJob, job.id)
    assert refreshed.last_status == "failed"
    assert refreshed.last_error


def test_due_jobs_run(session):
    rows = ensure_default_jobs(session)
    for row in rows:
        row.next_run_at = _now() - timedelta(minutes=1)
        row.enabled = row.job_type == "stress_forecast"
        session.add(row)
    session.commit()

    def fake_forecast(sess, _uid, _cfg):
        return {"status": "safe"}

    with patch.dict("app.core.scheduler.JOB_RUNNERS", {"stress_forecast": fake_forecast}):
        result = run_due_jobs(session, user_id="dev-local")

    assert result["ran"] >= 1
    assert any(r.get("ok") for r in result["results"])


def test_legacy_research_job_is_frozen(session):
    row = ScheduledJob(job_type="research_ingestion_check", enabled=True, last_status="success")
    session.add(row)
    session.commit()

    rows = ensure_default_jobs(session)
    frozen = next(r for r in rows if r.job_type == "research_ingestion_check")
    assert frozen.enabled is False
    assert json.loads(frozen.config_json)["deprecated"] is True
    out = run_job(session, frozen.id, user_id="dev-local")
    assert out["skipped"] == "disabled"


def test_opportunity_refresh_is_the_single_d003_scheduler_path(session):
    rows = ensure_default_jobs(session)
    job = next(r for r in rows if r.job_type == "opportunity_refresh")
    with (
        patch(
            "app.core.scheduler.run_v2_intelligence_refresh",
            return_value={"research": {"id": "d003-run", "status": "success"}, "stale_candidates": 1},
        ) as research,
    ):
        out = run_job(session, job.id, user_id="dev-local", force=True)
    assert out["ok"] is True
    assert out["result"]["research"]["id"] == "d003-run"
    assert out["result"]["stale_candidates"] == 1
    research.assert_called_once_with(session, trigger="scheduler")


def test_is_job_due_respects_next_run(session):
    rows = ensure_default_jobs(session)
    job = rows[0]
    job.next_run_at = _now() + timedelta(hours=2)
    job.enabled = True
    assert is_job_due(job) is False
    job.next_run_at = _now() - timedelta(minutes=1)
    assert is_job_due(job) is True


def test_compute_next_run_daily():
    start = _now()
    nxt = compute_next_run(start, "daily")
    assert nxt > start


def test_scheduler_http_api_is_retired(client):
    assert client.get("/api/scheduler/jobs").status_code == 404
