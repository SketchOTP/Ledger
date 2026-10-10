"""Backup, restore, and portable export tests."""

import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.backup_restore import (
    RESTORE_CONFIRM_TOKEN,
    backup_contains_env,
    backup_folder,
    build_ledger_export,
    create_backup,
    list_backups,
    redact_data,
    restore_backup,
)
from app.core.evidence import store_evidence
from app.core.user_ai_settings import save_user_ai_settings
from app.db.models import Bill, CapabilityProfile, OpportunityCandidate, OpportunityEvidence, OpportunityResearchRun
from app.main import app


@pytest.fixture(name="session")
def backup_session_fixture():
    """Use one isolated SQLite connection for real API calls and backup assertions."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(session):
    """Exercise the actual routes without leaking their database across the suite."""
    from app.api.deps import get_db

    def _test_db():
        yield session

    app.dependency_overrides[get_db] = _test_db
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        test_client.close()
        app.dependency_overrides.pop(get_db, None)


def test_backup_excludes_env(session, tmp_path, monkeypatch):
    from app.core import backup_restore as br

    monkeypatch.setattr(br, "backups_root", lambda _uid: tmp_path / "backups")
    monkeypatch.setattr(br, "household_db_path", lambda _uid: tmp_path / "ledgermind.db")
    (tmp_path / "ledgermind.db").write_bytes(b"sqlite-placeholder")
    events_dir = tmp_path / "events"
    events_dir.mkdir(parents=True)
    (events_dir / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (events_dir / "events-2026-05-27.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr("app.core.backup_restore.settings.event_log_dir", events_dir)

    session.add(Bill(name="Rent", amount=1000, required=True))
    session.commit()

    info = create_backup("dev-local", session, include_evidence=False)
    folder = tmp_path / "backups" / info["backup_id"]
    assert not backup_contains_env(folder)
    assert not any(p.name == ".env" for p in folder.rglob("*"))


def test_backup_includes_db(session, tmp_path, monkeypatch):
    from app.core import backup_restore as br

    db_path = tmp_path / "ledgermind.db"
    db_path.write_bytes(b"SQLite format 3\x00")
    monkeypatch.setattr(br, "backups_root", lambda _uid: tmp_path / "backups")
    monkeypatch.setattr(br, "household_db_path", lambda _uid: db_path)

    session.add(Bill(name="Utilities", amount=80, required=True))
    session.commit()

    info = create_backup("dev-local", session)
    folder = tmp_path / "backups" / info["backup_id"]
    assert (folder / "ledgermind.db").is_file()
    assert (folder / "full_backup.zip").is_file()
    with zipfile.ZipFile(folder / "full_backup.zip") as zf:
        names = zf.namelist()
    assert any(n.endswith("ledgermind.db") for n in names)


def test_backup_optionally_includes_evidence(session, tmp_path, monkeypatch):
    from app.core import backup_restore as br
    from app.core import evidence as ev_mod

    ev_dir = tmp_path / "evidence"
    monkeypatch.setattr(ev_mod, "EVIDENCE_DIR", ev_dir)
    monkeypatch.setattr(br, "backups_root", lambda _uid: tmp_path / "backups")
    monkeypatch.setattr(br, "household_db_path", lambda _uid: tmp_path / "ledgermind.db")
    (tmp_path / "ledgermind.db").write_bytes(b"SQLite format 3\x00")

    data = b"item,price\nmilk,3.49\n"
    store_evidence(
        session,
        filename="receipt.csv",
        content_type="text/csv",
        data=data,
        evidence_type="grocery_receipt",
    )

    without = create_backup("dev-local", session, include_evidence=False)
    folder_no = tmp_path / "backups" / without["backup_id"]
    assert not (folder_no / "evidence" / "files").exists() or not list((folder_no / "evidence" / "files").glob("*"))

    with_ev = create_backup("dev-local", session, include_evidence=True)
    folder_yes = tmp_path / "backups" / with_ev["backup_id"]
    files = list((folder_yes / "evidence" / "files").glob("*"))
    assert len(files) >= 1


def test_redacted_export_hides_sensitive_fields():
    payload = {
        "ai_api_key": "sk-secret",
        "nested": {"password_hash": "abc", "name": "Rent"},
    }
    redacted = redact_data(payload, redacted=True)
    assert redacted["ai_api_key"] == "[REDACTED]"
    assert redacted["nested"]["password_hash"] == "[REDACTED]"
    assert redacted["nested"]["name"] == "Rent"


def test_redacted_ledger_export(session):
    export = build_ledger_export(session, redacted=True)
    text = json.dumps(export)
    assert "sk-" not in text or "[REDACTED]" in text


def test_ledger_export_includes_d002_durable_records(session):
    session.add(CapabilityProfile(max_setup_hours=4))
    session.add(OpportunityCandidate(identity_key="d002-export", title="Durable candidate"))
    session.commit()
    tables = build_ledger_export(session)["tables"]
    assert tables["capability_profiles"][0]["max_setup_hours"] == 4
    assert tables["opportunity_candidates"][0]["title"] == "Durable candidate"


def test_ledger_export_includes_d003_research_audit_records(session):
    run = OpportunityResearchRun(trigger="manual", status="success")
    session.add(run)
    session.commit()
    session.refresh(run)
    session.add(
        OpportunityEvidence(
            research_run_id=run.id,
            provider_id="replay",
            source_url="https://example.test/source",
            claim_type="rate",
        )
    )
    session.commit()
    tables = build_ledger_export(session)["tables"]
    assert tables["opportunity_research_runs"][0]["id"] == run.id
    assert tables["opportunity_evidence"][0]["provider_id"] == "replay"


def test_ledger_export_includes_d004_outcome_records(session):
    from app.db.models import OpportunityCalibrationAudit, OpportunityOutcomeObservation, OpportunityPredictionSnapshot

    candidate = OpportunityCandidate(identity_key="d004-export", title="D004 export")
    session.add(candidate)
    session.commit()
    session.add(OpportunityPredictionSnapshot(candidate_id=candidate.id, prediction_json='{"expected_net_low": 1}'))
    session.add(OpportunityOutcomeObservation(candidate_id=candidate.id, outcome_status="failed"))
    session.add(OpportunityCalibrationAudit(dimension="category", dimension_value="other"))
    session.commit()
    tables = build_ledger_export(session)["tables"]
    assert tables["opportunity_prediction_snapshots"]
    assert tables["opportunity_outcome_observations"]
    assert tables["opportunity_calibration_audits"]


def test_restore_rejects_bad_version(session, tmp_path, monkeypatch):
    from app.core import backup_restore as br

    monkeypatch.setattr(br, "backups_root", lambda _uid: tmp_path / "backups")
    monkeypatch.setattr(br, "household_db_path", lambda _uid: tmp_path / "ledgermind.db")
    (tmp_path / "ledgermind.db").write_bytes(b"SQLite format 3\x00")

    info = create_backup("dev-local", session)
    folder = tmp_path / "backups" / info["backup_id"]
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["backup_format_version"] = 999
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported backup version"):
        restore_backup("dev-local", info["backup_id"], session, confirm=RESTORE_CONFIRM_TOKEN)


def test_restore_creates_pre_restore_backup(session, tmp_path, monkeypatch):
    from app.core import backup_restore as br

    db_path = tmp_path / "ledgermind.db"
    db_path.write_bytes(b"SQLite format 3\x00")
    monkeypatch.setattr(br, "backups_root", lambda _uid: tmp_path / "backups")
    monkeypatch.setattr(br, "household_db_path", lambda _uid: db_path)

    session.add(Bill(name="Before", amount=50, required=True))
    session.commit()
    original = create_backup("dev-local", session, label="original")

    db_path.write_bytes(b"SQLite format 3\x00-replaced")
    result = restore_backup("dev-local", original["backup_id"], session, confirm=RESTORE_CONFIRM_TOKEN)
    assert result["ok"] is True
    pre_id = result["pre_restore_backup_id"]
    assert pre_id.startswith("pre-restore-")
    backups = list_backups("dev-local")
    pre = [b for b in backups if b["backup_id"] == pre_id]
    assert len(pre) == 1


def test_api_create_and_list(client, session, tmp_path, monkeypatch):
    from app.core import backup_restore as br

    monkeypatch.setattr(br, "backups_root", lambda _uid: tmp_path / "backups")
    monkeypatch.setattr(br, "household_db_path", lambda _uid: tmp_path / "ledgermind.db")
    (tmp_path / "ledgermind.db").write_bytes(b"SQLite format 3\x00")

    res = client.post("/api/backup/create", json={"include_evidence": False})
    assert res.status_code == 200
    backup_id = res.json()["backup_id"]

    listed = client.get("/api/backup/list")
    assert listed.status_code == 200
    ids = [b["backup_id"] for b in listed.json()["backups"]]
    assert backup_id in ids


def test_api_restore_requires_confirm(client, session, tmp_path, monkeypatch):
    from app.core import backup_restore as br

    monkeypatch.setattr(br, "backups_root", lambda _uid: tmp_path / "backups")
    monkeypatch.setattr(br, "household_db_path", lambda _uid: tmp_path / "ledgermind.db")
    (tmp_path / "ledgermind.db").write_bytes(b"SQLite format 3\x00")

    created = client.post("/api/backup/create", json={})
    backup_id = created.json()["backup_id"]
    bad = client.post(
        "/api/backup/restore",
        json={"backup_id": backup_id, "confirm": "NOPE"},
    )
    assert bad.status_code == 400


def test_settings_backup_redacts_api_key(session, tmp_path, monkeypatch):
    from app.core import backup_restore as br
    from app.db.user_db import user_db_path

    monkeypatch.setattr(br, "backups_root", lambda _uid: tmp_path / "backups")
    monkeypatch.setattr(br, "household_db_path", lambda _uid: tmp_path / "ledgermind.db")
    (tmp_path / "ledgermind.db").write_bytes(b"SQLite format 3\x00")

    user_dir = user_db_path("dev-local").parent
    user_dir.mkdir(parents=True, exist_ok=True)
    save_user_ai_settings("dev-local", {"ai_provider": "openai", "ai_api_key": "sk-test-key"})

    info = create_backup("dev-local", session)
    folder = backup_folder("dev-local", info["backup_id"])
    ai_saved = json.loads((folder / "settings" / "ai_settings.json").read_text(encoding="utf-8"))
    assert ai_saved.get("ai_api_key") == "[REDACTED]"
