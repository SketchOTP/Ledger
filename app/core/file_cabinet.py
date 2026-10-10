"""Legacy File Cabinet archive and recovery support.

The standalone File Cabinet product was retired in V2. Its database rows and
uploaded bytes remain deliberately intact. This module is a read/export boundary
and must not mutate archive data as a side effect of inspection.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from app.config import ROOT_DIR, settings
from app.db.models import FileCabinetDocument, FileCabinetFolder
from app.db.user_db import user_db_path

ARCHIVE_VERSION = 1


def cabinet_storage_root(user_id: str) -> Path:
    """Return a user's Cabinet directory without creating it."""
    return user_db_path(user_id).parent / "file_cabinet"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _stored_path(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    # Legacy rows stored paths such as ``data/users/<id>/file_cabinet/...``
    # relative to the checkout.  Prefer that representation when it still
    # exists (including the D007 test fixtures), otherwise map it into the
    # explicit persistent state root used by the current checkout.
    legacy = ROOT_DIR / candidate
    if legacy.exists() or legacy.parent.exists():
        return legacy
    if candidate.parts and candidate.parts[0] == "data":
        return settings.data_dir.joinpath(*candidate.parts[1:])
    return settings.data_dir / candidate


def _safe_document_path(user_id: str, document: FileCabinetDocument) -> Path | None:
    candidate = _stored_path(document.file_path)
    root = cabinet_storage_root(user_id)
    if candidate is not None and _within(candidate, root):
        return candidate
    # A V2 checkout can contain a stale local ``data/users`` directory from
    # development while the real Cabinet bytes live under the external state
    # root.  For the historical ``data/users/<active-user>/...`` shape, retry
    # the state-root representation before rejecting the path.
    raw = Path(document.file_path or "")
    if len(raw.parts) >= 4 and raw.parts[:2] == ("data", "users") and raw.parts[2] == user_id:
        state_candidate = settings.data_dir.joinpath(*raw.parts[1:])
        if _within(state_candidate, root):
            return state_candidate
    return None


def _relative_storage_path(path: Path, root: Path) -> str | None:
    if not _within(path, root):
        return None
    return path.resolve().relative_to(root.resolve()).as_posix()


def _tags(document: FileCabinetDocument) -> list[str]:
    try:
        value = json.loads(document.tags_json or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(tag) for tag in value] if isinstance(value, list) else []


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _document_record(user_id: str, document: FileCabinetDocument, folder_names: dict[str, str]) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": document.id,
        "folder_id": document.folder_id,
        "folder_name": folder_names.get(document.folder_id),
        "title": document.title,
        "document_type": document.doc_type,
        "tags": _tags(document),
        "mime_type": document.mime_type,
        "note_text": document.note_text if document.doc_type == "note" else None,
        "created_at": _iso(document.created_at),
        "updated_at": _iso(document.updated_at),
        "file": None,
    }
    if document.doc_type != "file":
        return record
    root = cabinet_storage_root(user_id)
    path = _safe_document_path(user_id, document)
    if path is None:
        record["file"] = {
            "stored_relative_path": None,
            "exists": False,
            "integrity": "unsafe_path",
            "byte_size": None,
            "sha256": None,
        }
    elif not path.is_file():
        record["file"] = {
            "stored_relative_path": _relative_storage_path(path, root),
            "exists": False,
            "integrity": "missing",
            "byte_size": None,
            "sha256": None,
        }
    else:
        record["file"] = {
            "stored_relative_path": _relative_storage_path(path, root),
            "exists": True,
            "integrity": "verified",
            "byte_size": path.stat().st_size,
            "sha256": _sha256(path),
        }
    return record


def build_archive_manifest(session: Session, user_id: str) -> dict[str, Any]:
    """Build a non-mutating, user-scoped Cabinet archive inventory."""
    folders = session.exec(select(FileCabinetFolder).order_by(FileCabinetFolder.id)).all()
    folder_names = {folder.id: folder.name for folder in folders}
    rows = session.exec(select(FileCabinetDocument).order_by(FileCabinetDocument.id)).all()
    documents = [_document_record(user_id, row, folder_names) for row in rows]
    root = cabinet_storage_root(user_id)
    referenced = {
        record["file"]["stored_relative_path"] for record in documents if record["file"] and record["file"]["exists"]
    }
    orphans: list[dict[str, Any]] = []
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = _relative_storage_path(path, root)
            if relative is not None and relative not in referenced:
                orphans.append(
                    {
                        "stored_relative_path": relative,
                        "byte_size": path.stat().st_size,
                        "sha256": _sha256(path),
                    }
                )
    duplicate_groups: dict[str, list[str]] = defaultdict(list)
    for record in documents:
        file = record["file"]
        if file and file["exists"]:
            duplicate_groups[file["sha256"]].append(str(record["id"]))
    duplicates = [
        {"sha256": digest, "document_ids": ids} for digest, ids in sorted(duplicate_groups.items()) if len(ids) > 1
    ]
    missing = sum(1 for record in documents if record["file"] and record["file"]["integrity"] == "missing")
    unsafe = sum(1 for record in documents if record["file"] and record["file"]["integrity"] == "unsafe_path")
    return {
        "archive_version": ARCHIVE_VERSION,
        "folders": [
            {"id": folder.id, "name": folder.name, "created_at": _iso(folder.created_at)} for folder in folders
        ],
        "documents": documents,
        "orphans": orphans,
        "duplicates": duplicates,
        "summary": {
            "folder_count": len(folders),
            "document_count": len(documents),
            "note_count": sum(row.doc_type == "note" for row in rows),
            "file_count": sum(row.doc_type == "file" for row in rows),
            "verified_file_count": sum(1 for record in documents if record["file"] and record["file"]["exists"]),
            "missing_file_count": missing,
            "unsafe_path_count": unsafe,
            "orphan_file_count": len(orphans),
        },
    }


def archive_status(session: Session, user_id: str) -> dict[str, Any]:
    manifest = build_archive_manifest(session, user_id)
    return {
        "retired": True,
        "archive_version": ARCHIVE_VERSION,
        "summary": manifest["summary"],
        "has_data": bool(manifest["folders"] or manifest["documents"] or manifest["orphans"]),
    }


def resolve_document_path(session: Session, user_id: str, document_id: str) -> Path | None:
    """Resolve an existing legacy file only under the active user's root."""
    document = session.get(FileCabinetDocument, document_id)
    if document is None or document.doc_type != "file":
        return None
    path = _safe_document_path(user_id, document)
    return path if path and path.is_file() else None


def create_archive_export(session: Session, user_id: str) -> Path:
    """Create a temporary ZIP containing metadata, notes, verified files and orphans."""
    manifest = build_archive_manifest(session, user_id)
    root = cabinet_storage_root(user_id)
    handle = tempfile.NamedTemporaryFile(prefix="file-cabinet-archive-", suffix=".zip", delete=False)
    handle.close()
    archive_path = Path(handle.name)
    try:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
            for document in manifest["documents"]:
                if document["document_type"] == "note":
                    archive.writestr(f"notes/{document['id']}.txt", document["note_text"] or "")
                    continue
                file = document["file"]
                if not file or not file["exists"]:
                    continue
                path = resolve_document_path(session, user_id, document["id"])
                if path:
                    archive.write(path, f"files/{document['id']}_{path.name}")
            for orphan in manifest["orphans"]:
                path = root / orphan["stored_relative_path"]
                if _within(path, root) and path.is_file():
                    archive.write(path, f"orphans/{orphan['sha256'][:12]}_{path.name}")
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    return archive_path


def remove_temporary_archive(path: Path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
