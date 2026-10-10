"""D007 preservation and retirement coverage for the legacy File Cabinet."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_current_user, get_db
from app.core import backup_restore as br
from app.core import file_cabinet as cabinet
from app.core import household_reset as reset
from app.core.backup_restore import RESTORE_CONFIRM_TOKEN, create_backup, restore_backup
from app.db.master_models import User
from app.db.models import FileCabinetDocument, FileCabinetFolder
from app.main import app


def _configure(monkeypatch, tmp_path: Path, user_id: str = "dev-local") -> tuple[Path, Path]:
    user_dir = tmp_path / "data" / "users" / user_id
    db_path = user_dir / "ledgermind.db"
    monkeypatch.setattr(cabinet, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(cabinet, "user_db_path", lambda _user_id: db_path)
    monkeypatch.setattr(br, "user_db_path", lambda _user_id: db_path)
    monkeypatch.setattr(br, "household_db_path", lambda _user_id: db_path)
    monkeypatch.setattr(br, "backups_root", lambda _user_id: tmp_path / "backups")
    monkeypatch.setattr(br.settings, "event_log_dir", tmp_path / "events")
    monkeypatch.setattr(br.settings, "reports_dir", tmp_path / "reports")
    return user_dir / "file_cabinet", db_path


def _legacy_rows(session: Session, tmp_path: Path, user_id: str = "dev-local") -> dict[str, object]:
    root = tmp_path / "data" / "users" / user_id / "file_cabinet"
    root.mkdir(parents=True, exist_ok=True)
    default = FileCabinetFolder(id="default-folder", name="Bills")
    custom = FileCabinetFolder(id="custom-folder", name="Owner records")
    text_data = b"legacy cabinet text fixture\n"
    image_data = b"\x89PNG\r\n\x1a\nfixture"
    text_name = f"{hashlib.sha256(text_data).hexdigest()[:16]}_statement.txt"
    image_name = f"{hashlib.sha256(image_data).hexdigest()[:16]}_receipt.png"
    (root / text_name).write_bytes(text_data)
    (root / image_name).write_bytes(image_data)
    note = FileCabinetDocument(
        id="note-1",
        folder_id=custom.id,
        title="Important note",
        doc_type="note",
        tags_json='["tax", "2026"]',
        note_text="Preserve this owner-authored note.",
    )
    text = FileCabinetDocument(
        id="file-text",
        folder_id=default.id,
        title="Statement",
        doc_type="file",
        tags_json='["bill", "important"]',
        file_path=f"data/users/{user_id}/file_cabinet/{text_name}",
        mime_type="text/plain",
    )
    image = FileCabinetDocument(
        id="file-image",
        folder_id=custom.id,
        title="Receipt image",
        doc_type="file",
        tags_json='["receipt"]',
        file_path=f"data/users/{user_id}/file_cabinet/{image_name}",
        mime_type="image/png",
    )
    missing = FileCabinetDocument(
        id="file-missing",
        folder_id=custom.id,
        title="Missing file",
        doc_type="file",
        file_path=f"data/users/{user_id}/file_cabinet/missing.pdf",
        mime_type="application/pdf",
    )
    session.add_all([default, custom, note, text, image, missing])
    session.commit()
    return {"root": root, "note": note, "text": text, "image": image, "text_data": text_data, "image_data": image_data}


def test_manifest_export_and_archive_read_are_preserving(session, tmp_path, monkeypatch):
    root, _ = _configure(monkeypatch, tmp_path)
    rows = _legacy_rows(session, tmp_path)
    (root / "orphan.bin").write_bytes(b"orphan fixture")
    before = len(session.exec(select(FileCabinetFolder)).all())
    manifest = cabinet.build_archive_manifest(session, "dev-local")
    assert len(session.exec(select(FileCabinetFolder)).all()) == before
    assert manifest["summary"] == {
        "folder_count": 2,
        "document_count": 4,
        "note_count": 1,
        "file_count": 3,
        "verified_file_count": 2,
        "missing_file_count": 1,
        "unsafe_path_count": 0,
        "orphan_file_count": 1,
    }
    text = next(row for row in manifest["documents"] if row["id"] == "file-text")
    assert text["tags"] == ["bill", "important"]
    assert text["file"]["sha256"] == hashlib.sha256(rows["text_data"]).hexdigest()
    assert str(tmp_path) not in json.dumps(manifest)
    archive = cabinet.create_archive_export(session, "dev-local")
    try:
        with zipfile.ZipFile(archive) as exported:
            assert exported.read("notes/note-1.txt") == b"Preserve this owner-authored note."
            assert exported.read("files/file-text_" + Path(rows["text"].file_path).name) == rows["text_data"]
            assert exported.read("files/file-image_" + Path(rows["image"].file_path).name) == rows["image_data"]
            orphan_name = hashlib.sha256(b"orphan fixture").hexdigest()[:12] + "_orphan.bin"
            assert exported.read("orphans/" + orphan_name) == b"orphan fixture"
    finally:
        cabinet.remove_temporary_archive(archive)


def test_document_resolution_rejects_escape_and_other_user_storage(session, tmp_path, monkeypatch):
    root, _ = _configure(monkeypatch, tmp_path)
    rows = _legacy_rows(session, tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("must not be exposed", encoding="utf-8")
    other_root = tmp_path / "data" / "users" / "other-user" / "file_cabinet"
    other_root.mkdir(parents=True)
    other_file = other_root / "other.txt"
    other_file.write_text("other user", encoding="utf-8")
    session.add_all(
        [
            FileCabinetDocument(id="escape", folder_id="default-folder", title="escape", file_path="../outside.txt"),
            FileCabinetDocument(id="other-user", folder_id="default-folder", title="other", file_path=str(other_file)),
        ]
    )
    session.commit()
    resolved = cabinet.resolve_document_path(session, "dev-local", rows["text"].id)
    assert resolved == root / Path(rows["text"].file_path).name
    assert cabinet.resolve_document_path(session, "dev-local", "escape") is None
    assert cabinet.resolve_document_path(session, "dev-local", "other-user") is None
    assert cabinet.build_archive_manifest(session, "dev-local")["summary"]["unsafe_path_count"] == 2


def test_minimal_archive_api_preserves_download_and_retires_writes(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    rows = _legacy_rows(session, tmp_path)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: User(id="dev-local", username="dev")
    client = TestClient(app)
    try:
        status = client.get("/api/file-cabinet/status")
        assert status.status_code == 200, status.text
        assert status.json()["retired"] is True
        assert client.get("/api/file-cabinet/documents/file-text/download").content == rows["text_data"]
        export = client.post("/api/file-cabinet/export")
        assert export.status_code == 200 and export.headers["content-type"].startswith("application/zip")
        assert client.post("/api/file-cabinet/folders?name=New").status_code in {404, 405}
        assert client.post("/api/file-cabinet/notes").status_code in {404, 405}
        assert client.post("/api/file-cabinet/upload").status_code in {404, 405}
        assert client.delete("/api/file-cabinet/documents/file-text").status_code == 405
    finally:
        client.close()
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        session.close()


def test_backup_restore_round_trip_preserves_cabinet_rows_and_bytes(tmp_path, monkeypatch):
    _, db_path = _configure(monkeypatch, tmp_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    try:
        rows = _legacy_rows(session, tmp_path)
        info = create_backup("dev-local", session, label="cabinet-roundtrip")
        backup_root = tmp_path / "backups" / info["backup_id"]
        stored_name = Path(rows["text"].file_path).name
        saved = backup_root / "file_cabinet" / "files" / stored_name
        assert saved.read_bytes() == rows["text_data"]
        assert hashlib.sha256(saved.read_bytes()).hexdigest() == hashlib.sha256(rows["text_data"]).hexdigest()
        assert json.loads((backup_root / "manifest.json").read_text())["contents"]["file_cabinet_files"] == 2
        with sqlite3.connect(backup_root / "ledgermind.db") as backed_up:
            assert (
                backed_up.execute("SELECT title FROM file_cabinet_documents WHERE id = ?", ("note-1",)).fetchone()[0]
                == "Important note"
            )
        (rows["root"] / stored_name).unlink()
        rows["note"].title = "Changed after backup"
        session.add(rows["note"])
        session.commit()
        assert restore_backup("dev-local", info["backup_id"], session, confirm=RESTORE_CONFIRM_TOKEN)["ok"] is True
        assert (rows["root"] / stored_name).read_bytes() == rows["text_data"]
    finally:
        session.close()
    with sqlite3.connect(db_path) as restored:
        title = restored.execute("SELECT title FROM file_cabinet_documents WHERE id = ?", ("note-1",)).fetchone()[0]
        assert title == "Important note"
        assert restored.execute("SELECT COUNT(*) FROM file_cabinet_folders").fetchone()[0] == 2


def test_explicit_household_reset_removes_archived_bytes_but_archive_reads_do_not(session, tmp_path, monkeypatch):
    root, db_path = _configure(monkeypatch, tmp_path)
    rows = _legacy_rows(session, tmp_path)
    # Archive inspection is not a reset and leaves user bytes untouched.
    cabinet.archive_status(session, "dev-local")
    assert (root / Path(rows["text"].file_path).name).is_file()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(b"reset fixture")
    monkeypatch.setattr(reset, "user_db_path", lambda _user_id: db_path)
    monkeypatch.setattr(reset, "clear_engine_caches", lambda: None)
    monkeypatch.setattr(reset, "ensure_user_db", lambda _user_id: db_path.write_bytes(b"new schema"))
    reset.reset_user_household_files("dev-local")
    assert db_path.read_bytes() == b"new schema"
    assert not root.exists()


def test_v2_and_evidence_modules_do_not_depend_on_file_cabinet_product():
    root = Path(__file__).parents[1]
    for relative in (
        "core/financial_snapshot.py",
        "core/liquidity_planner.py",
        "core/stress_forecast.py",
        "core/v2_opportunities.py",
        "core/v2_outcome_learning.py",
        "core/v2_today.py",
        "core/evidence.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "app.core.file_cabinet" not in source, relative
        assert "FileCabinet" not in source, relative


def test_frontend_route_is_legacy_archive_not_management_page():
    frontend = Path(__file__).parents[2] / "frontend" / "src"
    assert "LegacyFileCabinetArchive" in (frontend / "App.tsx").read_text(encoding="utf-8")
    assert "DigitalFileCabinet" not in (frontend / "App.tsx").read_text(encoding="utf-8")
    assert 'path="/file-cabinet"' in (frontend / "App.tsx").read_text(encoding="utf-8")
