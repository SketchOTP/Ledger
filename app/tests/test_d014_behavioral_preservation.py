"""D014 behavioral-sidecar preservation, privacy, and retirement contracts."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from sqlmodel import Session

from app.core import backup_restore as br
from app.core import household_reset as reset
from app.core.backup_restore import RESTORE_CONFIRM_TOKEN, create_backup, restore_backup
from app.core.behavioral_store import BEHAVIORAL_FILENAMES
from app.core.owner_context import build_owner_context, build_provider_owner_context
from app.db.models import CapabilityAsset, CapabilityProfile, CapabilitySkill


def _configure(monkeypatch, tmp_path: Path) -> dict[str, Path]:
    user_dir = tmp_path / "users" / "dev-local"
    paths = {name: user_dir / name for name in BEHAVIORAL_FILENAMES}
    db_path = user_dir / "ledgermind.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(b"SQLite format 3\x00")
    monkeypatch.setattr(br, "behavioral_file_paths", lambda _uid: paths)
    monkeypatch.setattr(br, "household_db_path", lambda _uid: db_path)
    monkeypatch.setattr(br, "backups_root", lambda _uid: tmp_path / "backups")
    return paths


def test_backup_manifest_explicitly_includes_populated_behavioral_sidecars(session, tmp_path, monkeypatch):
    paths = _configure(monkeypatch, tmp_path)
    payloads = {
        "life_goals.json": b'{"meaning":["protect the archive"]}',
        "risk_checklist.json": b'{"will":"complete"}',
        "human_capital.json": b'[{"id":"hc-1","type":"course","notes":"private note"}]',
    }
    for name, payload in payloads.items():
        paths[name].write_bytes(payload)

    info = create_backup("dev-local", session, label="behavioral-populated")
    folder = tmp_path / "backups" / info["backup_id"]
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    entries = {row["filename"]: row for row in manifest["contents"]["behavioral_files"]}
    assert set(entries) == set(BEHAVIORAL_FILENAMES)
    for name, payload in payloads.items():
        entry = entries[name]
        assert entry["present"] is True
        assert entry["size_bytes"] == len(payload)
        assert entry["sha256"] == hashlib.sha256(payload).hexdigest()
        assert (folder / entry["archive_path"]).read_bytes() == payload
    with zipfile.ZipFile(folder / "full_backup.zip") as archive:
        assert {f"behavioral/{name}" for name in BEHAVIORAL_FILENAMES} <= set(archive.namelist())


def test_backup_manifest_records_absent_behavioral_sidecars(session, tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    info = create_backup("dev-local", session, label="behavioral-empty")
    folder = tmp_path / "backups" / info["backup_id"]
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    entries = manifest["contents"]["behavioral_files"]
    assert all(row["present"] is False for row in entries)
    assert all(row["sha256"] is None for row in entries)


def test_restore_round_trip_preserves_exact_bytes_and_old_format2_is_non_destructive(
    session, tmp_path, monkeypatch
):
    paths = _configure(monkeypatch, tmp_path)
    original = {
        "life_goals.json": b'{"meaning":["keep exact bytes"]}\n',
        "risk_checklist.json": b'{"will":"partial"}\n',
        "human_capital.json": b'[{"id":"hc-1","notes":"do not normalize"}]\n',
    }
    for name, payload in original.items():
        paths[name].write_bytes(payload)
    info = create_backup("dev-local", session, label="behavioral-restore")
    for name in BEHAVIORAL_FILENAMES:
        paths[name].write_bytes(b"changed")

    assert restore_backup("dev-local", info["backup_id"], session, confirm=RESTORE_CONFIRM_TOKEN)["ok"] is True
    for name, payload in original.items():
        assert paths[name].read_bytes() == payload

    # A pre-D014 format-2 backup has no explicit behavioral inventory.  Its
    # restore must not erase sidecars that exist in the target household.
    folder = tmp_path / "backups" / info["backup_id"]
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["contents"].pop("behavioral_files", None)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    sentinel = b"target sidecar remains"
    paths["life_goals.json"].write_bytes(sentinel)
    with Session(session.get_bind()) as second_session:
        restore_backup("dev-local", info["backup_id"], second_session, confirm=RESTORE_CONFIRM_TOKEN)
    assert paths["life_goals.json"].read_bytes() == sentinel


def test_household_reset_removes_behavioral_sidecars(monkeypatch, tmp_path):
    user_dir = tmp_path / "users" / "dev-local"
    db_path = user_dir / "ledgermind.db"
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"reset fixture")
    paths = {name: user_dir / name for name in BEHAVIORAL_FILENAMES}
    for path in paths.values():
        path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(reset, "user_db_path", lambda _uid: db_path)
    monkeypatch.setattr(reset, "behavioral_file_paths", lambda _uid: paths)
    monkeypatch.setattr(reset, "cabinet_storage_root", lambda _uid: user_dir / "file_cabinet")
    monkeypatch.setattr(reset, "clear_engine_caches", lambda: None)
    monkeypatch.setattr(reset, "ensure_user_db", lambda _uid: db_path.write_bytes(b"new schema"))

    reset.reset_user_household_files("dev-local")
    assert db_path.read_bytes() == b"new schema"
    assert all(not path.exists() for path in paths.values())


def test_owner_context_keeps_behavioral_signals_local_and_preserves_d002_precedence(session, monkeypatch):
    from app.core import owner_context as context

    session.add(CapabilityProfile(prefers_automated=True, max_setup_hours=4, notes="canonical profile"))
    session.add(CapabilitySkill(name="Python", category="software"))
    session.add(CapabilityAsset(name="Template kit", asset_type="digital"))
    session.commit()
    monkeypatch.setattr(context, "load_life_goals", lambda _uid: {"meaning": ["private goal"]})
    monkeypatch.setattr(context, "load_risk_checklist", lambda _uid: {"will": "complete"})
    monkeypatch.setattr(
        context,
        "load_human_capital_records",
        lambda _uid: [{"id": "hc-1", "notes": "private human-capital note"}],
    )

    owner = build_owner_context(session, user_id="dev-local")
    provider = build_provider_owner_context(owner)
    provider_text = json.dumps(provider)
    assert owner["priorities"]["meaning"] == ["private goal"]
    assert owner["human_capital"][0]["notes"] == "private human-capital note"
    assert "private goal" not in provider_text
    assert "private human-capital note" not in provider_text
    assert provider["capability"]["skills"] == [{"name": "Python", "category": "software"}]
    assert provider["capability"]["prefers_automated"] is True


def test_behavioral_product_surface_is_retired_without_removing_sidecar_support():
    frontend = Path(__file__).parents[2] / "frontend" / "src"
    app_source = (frontend / "App.tsx").read_text(encoding="utf-8")
    assert 'const BehavioralFinance = lazy(() => import("./pages/BehavioralFinance"));' not in app_source
    assert '<Route path="/behavioral-finance" element={<Navigate to="/" replace />} />' in app_source
    assert not (frontend / "pages" / "BehavioralFinance.tsx").exists()
    assert not (Path(__file__).parents[2] / "app" / "api" / "routes_behavioral.py").exists()
