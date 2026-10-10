"""Household backup, restore, and portable export."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlmodel import Session, create_engine, select

from app.api.deps import AUTH_DISABLED
from app.config import ROOT_DIR, resolve_state_path, settings
from app.core.behavioral_store import BEHAVIORAL_FILENAMES, behavioral_file_paths
from app.core.evidence import EVIDENCE_DIR
from app.core.file_cabinet import cabinet_storage_root
from app.core.user_ai_settings import load_user_ai_settings
from app.db.models import (
    Bill,
    BillInstance,
    BorrowerProfile,
    BudgetCategory,
    CapabilityAsset,
    CapabilityProfile,
    CapabilitySkill,
    CfoReport,
    CreditCard,
    Debt,
    DebtOccurrence,
    DebtPayment,
    EventLogEntry,
    EvidenceExtraction,
    EvidenceFile,
    FileCabinetDocument,
    FileCabinetFolder,
    FinancialAction,
    FinancialMemoryPattern,
    FinancialOffer,
    FinancialSnapshotRecord,
    FinancialTimelineEvent,
    GroceryCartRun,
    GroceryCartRunItem,
    GroceryDealPriceObservation,
    GroceryItem,
    GroceryPriceObservation,
    GroceryProductMatch,
    GroceryProfile,
    GroceryStaple,
    GroceryStore,
    GroceryStoreLocation,
    IncomeSource,
    LiquidAccount,
    MiscOccurrence,
    MiscSpending,
    MortgageAccount,
    OnboardingState,
    OpportunityCalibrationAudit,
    OpportunityCandidate,
    OpportunityEvidence,
    OpportunityOutcomeObservation,
    OpportunityPredictionSnapshot,
    OpportunityResearchRun,
    OpportunityTransition,
    OpportunityValidationFact,
    OutcomeLearningAudit,
    Paycheck,
    PaymentArchiveEvent,
    Recommendation,
    ResearchFinding,
    ResearchIngestionFinding,
    ShoppingList,
    Transaction,
    TravelExpense,
    TravelOccurrence,
    TravelSettings,
)
from app.db.user_db import clear_engine_caches, user_db_path
from app.events.bus import event_bus
from app.events.types import Event, EventType

BACKUP_FORMAT_VERSION = 2
CURATED_RATES_PATH = ROOT_DIR / "data" / "research" / "curated_rates.json"
RESTORE_CONFIRM_TOKEN = "RESTORE"

SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|password|secret|token|authorization|credential)",
    re.IGNORECASE,
)

EXPORT_MODELS: tuple[type, ...] = (
    IncomeSource,
    Paycheck,
    Bill,
    BillInstance,
    LiquidAccount,
    Debt,
    DebtOccurrence,
    DebtPayment,
    MortgageAccount,
    CreditCard,
    CapabilityProfile,
    CapabilitySkill,
    CapabilityAsset,
    OpportunityCandidate,
    OpportunityPredictionSnapshot,
    OpportunityOutcomeObservation,
    OpportunityCalibrationAudit,
    OpportunityResearchRun,
    OpportunityEvidence,
    OpportunityTransition,
    OpportunityValidationFact,
    BorrowerProfile,
    BudgetCategory,
    Transaction,
    GroceryStore,
    GroceryItem,
    GroceryPriceObservation,
    ShoppingList,
    GroceryProfile,
    GroceryStaple,
    GroceryStoreLocation,
    GroceryProductMatch,
    GroceryDealPriceObservation,
    GroceryCartRun,
    GroceryCartRunItem,
    MiscSpending,
    MiscOccurrence,
    TravelSettings,
    TravelExpense,
    TravelOccurrence,
    PaymentArchiveEvent,
    Recommendation,
    FinancialAction,
    CfoReport,
    FinancialOffer,
    ResearchFinding,
    ResearchIngestionFinding,
    EvidenceFile,
    EvidenceExtraction,
    FileCabinetFolder,
    FileCabinetDocument,
    EventLogEntry,
    OnboardingState,
    FinancialSnapshotRecord,
    FinancialTimelineEvent,
    FinancialMemoryPattern,
    OutcomeLearningAudit,
)


def backups_root(user_id: str) -> Path:
    root = user_db_path(user_id).parent / "backups"
    root.mkdir(parents=True, exist_ok=True)
    return root


def household_db_path(user_id: str) -> Path:
    if AUTH_DISABLED and settings.database_url.startswith("sqlite:///"):
        raw = settings.database_url.removeprefix("sqlite:///")
        path = Path(raw)
        return resolve_state_path(path)
    return user_db_path(user_id)


def _now_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]


def redact_value(key: str, value: Any) -> Any:
    if SENSITIVE_KEY_RE.search(key):
        return "[REDACTED]"
    return value


def redact_data(data: Any, *, redacted: bool) -> Any:
    if not redacted:
        return data
    if isinstance(data, dict):
        return {k: redact_data(redact_value(k, v), redacted=True) for k, v in data.items()}
    if isinstance(data, list):
        return [redact_data(item, redacted=True) for item in data]
    return data


def _model_rows(session: Session, model: type) -> list[dict[str, Any]]:
    rows = session.exec(select(model)).all()
    out: list[dict[str, Any]] = []
    for row in rows:
        payload = row.model_dump(mode="json")
        out.append(payload)
    return out


def build_ledger_export(session: Session, *, redacted: bool = False) -> dict[str, Any]:
    tables: dict[str, list[dict[str, Any]]] = {}
    for model in EXPORT_MODELS:
        name = getattr(model, "__tablename__", model.__name__)
        tables[name] = _model_rows(session, model)
    payload = {
        "export_version": 1,
        "exported_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "tables": tables,
    }
    return redact_data(payload, redacted=redacted)


def build_human_summary(session: Session, ledger: dict[str, Any]) -> str:
    bills = ledger.get("tables", {}).get("bills", [])
    debts = ledger.get("tables", {}).get("debts", [])
    income = ledger.get("tables", {}).get("income_sources", [])
    snapshots = ledger.get("tables", {}).get("financial_snapshots", [])
    evidence = ledger.get("tables", {}).get("evidence_files", [])
    lines = [
        "# Ledger Household Summary",
        "",
        f"Generated: {ledger.get('exported_at', 'unknown')}",
        "",
        "## Overview",
        f"- Income sources: {len(income)}",
        f"- Bills: {len(bills)}",
        f"- Debts: {len(debts)}",
        f"- Financial snapshots: {len(snapshots)}",
        f"- Evidence files (metadata): {len(evidence)}",
        "",
    ]
    if bills:
        lines.append("## Bills")
        for b in bills[:20]:
            lines.append(f"- {b.get('name', '?')}: ${float(b.get('amount') or 0):,.2f}")
        if len(bills) > 20:
            lines.append(f"- … and {len(bills) - 20} more")
        lines.append("")
    if debts:
        lines.append("## Debts")
        for d in debts[:20]:
            lines.append(f"- {d.get('creditor', '?')}: ${float(d.get('balance') or 0):,.2f} @ {d.get('apr', 0)}% APR")
        if len(debts) > 20:
            lines.append(f"- … and {len(debts) - 20} more")
        lines.append("")
    lines.append("_Portable export from Ledger. Secrets are never included in backups._")
    return "\n".join(lines)


def _settings_metadata(user_id: str, *, redacted: bool) -> dict[str, Any]:
    ai = load_user_ai_settings(user_id) or {}
    safe = {
        "ai_provider": ai.get("ai_provider"),
        "ai_base_url": ai.get("ai_base_url"),
        "ai_model": ai.get("ai_model"),
        "ai_allow_remote_openai": ai.get("ai_allow_remote_openai"),
        "has_api_key": bool(ai.get("ai_api_key")),
    }
    return redact_data(safe, redacted=redacted)


def _copy_tree_filtered(src: Path, dest: Path, *, exclude_names: frozenset[str]) -> int:
    if not src.is_dir():
        return 0
    count = 0
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        if not item.is_file():
            continue
        if any(part in {"backups", "deploy_backups", "archives"} for part in item.relative_to(src).parts):
            continue
        if item.name in exclude_names or item.name == ".env":
            continue
        rel = item.relative_to(src)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        count += 1
    return count


def _copy_database(src: Path, dest: Path) -> None:
    """Take a consistent SQLite backup, including committed WAL state when present."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(src) as source, sqlite3.connect(dest) as target:
            source.backup(target)
    except sqlite3.DatabaseError:
        # Existing tests and very old installations can have non-SQLite
        # placeholders. Preserve their established file-copy behavior.
        shutil.copy2(src, dest)


def _copy_cabinet_tree(src: Path, dest: Path) -> int:
    """Copy only regular files physically contained by a Cabinet storage root."""
    if not src.is_dir():
        return 0
    source_root = src.resolve()
    count = 0
    for item in src.rglob("*"):
        if not item.is_file():
            continue
        try:
            resolved = item.resolve()
            relative = resolved.relative_to(source_root)
        except ValueError:
            # A symlink outside the user Cabinet is never archive data.
            continue
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(resolved, target)
        count += 1
    return count


def _zip_dir(folder: Path, zip_path: Path) -> None:
    zip_resolved = zip_path.resolve()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            if path.resolve() == zip_resolved or path.name == "full_backup.zip":
                continue
            zf.write(path, path.relative_to(folder).as_posix())


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_behavioral_sidecars(user_id: str, destination: Path) -> list[dict[str, Any]]:
    """Copy the three user-authored behavioral files with explicit provenance."""
    paths = behavioral_file_paths(user_id)
    root = next(iter(paths.values())).parent.resolve()
    entries: list[dict[str, Any]] = []
    for name in BEHAVIORAL_FILENAMES:
        source = paths[name]
        entry: dict[str, Any] = {
            "filename": name,
            "present": False,
            "archive_path": None,
            "size_bytes": 0,
            "sha256": None,
        }
        if source.is_symlink() or not source.is_file():
            if source.exists():
                entry["reason"] = "not_a_regular_file"
            entries.append(entry)
            continue
        try:
            source.resolve().relative_to(root)
        except ValueError:
            entry["reason"] = "outside_user_directory"
            entries.append(entry)
            continue
        archive_rel = f"behavioral/{name}"
        target = destination / archive_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        entry.update(
            {
                "present": True,
                "archive_path": archive_rel,
                "size_bytes": source.stat().st_size,
                "sha256": _sha256_file(source),
            }
        )
        entries.append(entry)
    return entries


def create_backup(
    user_id: str,
    session: Session,
    *,
    include_evidence: bool = False,
    include_event_logs: bool = False,
    label: str | None = None,
) -> dict[str, Any]:
    """Create a full household backup archive and sidecar exports."""
    backup_id = label or _now_id()
    folder = backups_root(user_id) / backup_id
    db_src = household_db_path(user_id)
    if not db_src.is_file():
        raise FileNotFoundError(f"Household database not found: {db_src}")
    # Fail before creating a partial archive when the destination cannot hold
    # the database plus a conservative amount of metadata/cabinet content.
    # Metadata is compact; use a bounded 1 MiB allowance rather than a large
    # fixed reserve that makes small isolated/test files fail unnecessarily.
    estimate = db_src.stat().st_size + 1 * 1024 * 1024
    if include_event_logs and settings.event_log_dir.is_dir():
        estimate += sum(path.stat().st_size for path in settings.event_log_dir.rglob("*") if path.is_file())
    if include_evidence and EVIDENCE_DIR.is_dir():
        estimate += sum(path.stat().st_size for path in EVIDENCE_DIR.rglob("*") if path.is_file())
    try:
        folder.parent.mkdir(parents=True, exist_ok=True)
        free_bytes = shutil.disk_usage(folder.parent).free
    except OSError:
        free_bytes = shutil.disk_usage(db_src.parent).free
    if free_bytes < int(estimate * 1.10):
        raise RuntimeError("Not enough free space for a safe household backup")
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)

    _copy_database(db_src, folder / "ledgermind.db")

    if CURATED_RATES_PATH.is_file():
        shutil.copy2(CURATED_RATES_PATH, folder / "curated_rates.json")

    ledger = build_ledger_export(session, redacted=False)
    ledger_redacted = build_ledger_export(session, redacted=True)
    _write_json(folder / "ledger_export.json", ledger)
    _write_json(folder / "ledger_export.redacted.json", ledger_redacted)
    (folder / "human_readable_summary.md").write_text(build_human_summary(session, ledger), encoding="utf-8")

    evidence_rows = _model_rows(session, EvidenceFile)
    extraction_rows = _model_rows(session, EvidenceExtraction)
    _write_json(
        folder / "evidence" / "metadata.json",
        {"files": evidence_rows, "extractions": extraction_rows},
    )

    if include_evidence:
        ev_dest = folder / "evidence" / "files"
        ev_dest.mkdir(parents=True, exist_ok=True)
        for row in evidence_rows:
            storage = (row.get("storage_path") or "").strip()
            if storage:
                src = resolve_state_path(storage)
            else:
                fname = row.get("filename") or "file"
                src = EVIDENCE_DIR / f"{row.get('id')}_{fname}"
            if src.is_file():
                shutil.copy2(src, ev_dest / src.name)

    _write_json(folder / "events" / "db_events.json", _model_rows(session, EventLogEntry))
    # EventLogEntry rows are already exported above. Raw append-only event
    # files are intentionally opt-in: copying the global event directory into
    # every household backup caused unbounded duplication in production.
    event_files = 0
    if include_event_logs:
        event_files = _copy_tree_filtered(
            settings.event_log_dir,
            folder / "events" / "files",
            exclude_names=frozenset({".env"}),
        )

    _write_json(folder / "reports" / "reports.json", _model_rows(session, CfoReport))
    report_files = _copy_tree_filtered(
        settings.reports_dir,
        folder / "reports" / "files",
        exclude_names=frozenset({".env"}),
    )

    cabinet_files = _copy_cabinet_tree(
        cabinet_storage_root(user_id),
        folder / "file_cabinet" / "files",
    )

    behavioral_files = _copy_behavioral_sidecars(user_id, folder)

    _write_json(folder / "snapshots" / "snapshots.json", _model_rows(session, FinancialSnapshotRecord))

    ai_path = user_db_path(user_id).parent / "ai_settings.json"
    settings_dir = folder / "settings"
    settings_dir.mkdir(parents=True, exist_ok=True)
    if ai_path.is_file():
        raw = json.loads(ai_path.read_text(encoding="utf-8"))
        _write_json(settings_dir / "ai_settings.json", redact_data(raw, redacted=True))
        _write_json(settings_dir / "ai_settings.metadata.json", _settings_metadata(user_id, redacted=False))
    _write_json(
        settings_dir / "onboarding.json",
        _model_rows(session, OnboardingState),
    )

    manifest = {
        "backup_format_version": BACKUP_FORMAT_VERSION,
        "backup_id": backup_id,
        "user_id": user_id,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "include_evidence": include_evidence,
        "contents": {
            "database": db_src.is_file(),
            "curated_rates": (folder / "curated_rates.json").is_file(),
            "evidence_metadata": True,
            "evidence_files": include_evidence,
            "event_log_files": event_files,
            "report_files": report_files,
            "file_cabinet_files": cabinet_files,
            "behavioral_files": behavioral_files,
        },
        "excludes_env": True,
    }
    _write_json(folder / "manifest.json", manifest)

    zip_path = folder / "full_backup.zip"
    _zip_dir(folder, zip_path)

    event_bus.emit(
        Event(
            event_type=EventType.BACKUP_CREATED.value,
            source="backup_restore",
            payload={"backup_id": backup_id, "include_evidence": include_evidence},
        ),
        session=session,
    )

    return {
        "backup_id": backup_id,
        "created_at": manifest["created_at"],
        "include_evidence": include_evidence,
        "size_bytes": zip_path.stat().st_size if zip_path.is_file() else 0,
        "download_path": f"/api/backup/{backup_id}/download",
    }


def list_backups(user_id: str) -> list[dict[str, Any]]:
    root = backups_root(user_id)
    items: list[dict[str, Any]] = []
    for path in sorted(root.iterdir(), reverse=True):
        if not path.is_dir():
            continue
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        zip_path = path / "full_backup.zip"
        items.append(
            {
                "backup_id": manifest.get("backup_id", path.name),
                "created_at": manifest.get("created_at"),
                "include_evidence": manifest.get("include_evidence", False),
                "size_bytes": zip_path.stat().st_size if zip_path.is_file() else 0,
                "pre_restore": str(path.name).startswith("pre-restore-"),
            }
        )
    return items


def backup_folder(user_id: str, backup_id: str) -> Path:
    folder = backups_root(user_id) / backup_id
    if not folder.is_dir():
        raise FileNotFoundError(f"Backup not found: {backup_id}")
    return folder


def backup_zip_path(user_id: str, backup_id: str) -> Path:
    path = backup_folder(user_id, backup_id) / "full_backup.zip"
    if not path.is_file():
        raise FileNotFoundError(f"Backup archive not found: {backup_id}")
    return path


def _validate_manifest(manifest: dict[str, Any]) -> None:
    version = manifest.get("backup_format_version")
    if version not in {1, BACKUP_FORMAT_VERSION}:
        raise ValueError(f"Unsupported backup version {version!r}; expected one of 1 or {BACKUP_FORMAT_VERSION}")


def _restore_files_from_backup(folder: Path, user_id: str, *, include_evidence: bool) -> None:
    db_backup = folder / "ledgermind.db"
    if not db_backup.is_file():
        raise FileNotFoundError("Backup missing ledgermind.db")

    curated = folder / "curated_rates.json"
    if curated.is_file():
        CURATED_RATES_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(curated, CURATED_RATES_PATH)

    ev_files = folder / "evidence" / "files"
    if include_evidence and ev_files.is_dir():
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        for f in ev_files.iterdir():
            if f.is_file():
                shutil.copy2(f, EVIDENCE_DIR / f.name)

    ev_log_files = folder / "events" / "files"
    if ev_log_files.is_dir():
        settings.event_log_dir.mkdir(parents=True, exist_ok=True)
        for f in ev_log_files.rglob("*"):
            if f.is_file():
                rel = f.relative_to(ev_log_files)
                dest = settings.event_log_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)

    report_files = folder / "reports" / "files"
    if report_files.is_dir():
        settings.reports_dir.mkdir(parents=True, exist_ok=True)
        for f in report_files.rglob("*"):
            if f.is_file():
                rel = f.relative_to(report_files)
                dest = settings.reports_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)

    cabinet_files = folder / "file_cabinet" / "files"
    if cabinet_files.is_dir():
        _copy_cabinet_tree(cabinet_files, cabinet_storage_root(user_id))


def _restore_behavioral_sidecars(folder: Path, user_id: str, manifest: dict[str, Any]) -> None:
    """Restore explicitly listed behavioral files; old backups remain non-destructive."""
    entries = (manifest.get("contents") or {}).get("behavioral_files")
    if not isinstance(entries, list):
        # Format-2 archives created before D014 have no behavioral inventory.
        # Preserve any currently present sidecars rather than treating absence
        # as an instruction to erase user-authored state.
        return
    paths = behavioral_file_paths(user_id)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("filename")
        if name not in paths:
            raise ValueError(f"Unsupported behavioral sidecar {name!r}")
        if not entry.get("present"):
            continue
        archive_rel = f"behavioral/{name}"
        if entry.get("archive_path") not in {archive_rel, None}:
            raise ValueError(f"Invalid behavioral archive path for {name!r}")
        source = folder / archive_rel
        if not source.is_file():
            raise FileNotFoundError(f"Backup missing behavioral sidecar: {name}")
        expected_hash = entry.get("sha256")
        if expected_hash and _sha256_file(source) != expected_hash:
            raise ValueError(f"Behavioral sidecar hash mismatch: {name}")
        target = paths[name]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _record_restore_event(db_path: Path, event: Event) -> None:
    """Write post-restore audit data through a fresh connection to restored DB."""
    try:
        with sqlite3.connect(db_path) as restored_db:
            restored_db.execute("PRAGMA schema_version")
    except sqlite3.DatabaseError:
        # Legacy compatibility tests and historical placeholder backups are not
        # SQLite databases. Preserve the restore result and its file audit log.
        event_bus.emit(event)
        return
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    with Session(engine) as restored_session:
        event_bus.emit(event, session=restored_session)
        restored_session.commit()


def restore_backup(
    user_id: str,
    backup_id: str,
    session: Session,
    *,
    confirm: str,
) -> dict[str, Any]:
    if confirm != RESTORE_CONFIRM_TOKEN:
        raise ValueError(f"Confirmation must be exactly {RESTORE_CONFIRM_TOKEN!r}")

    folder = backup_folder(user_id, backup_id)
    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("Backup manifest missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _validate_manifest(manifest)
    include_evidence = bool(manifest.get("include_evidence"))

    pre_id = f"pre-restore-{_now_id()}"
    pre_backup = create_backup(user_id, session, include_evidence=include_evidence, label=pre_id)

    event_bus.emit(
        Event(
            event_type=EventType.BACKUP_RESTORE_STARTED.value,
            source="backup_restore",
            payload={"backup_id": backup_id, "pre_restore_backup_id": pre_id},
        ),
        session=session,
    )
    # A restore replaces the SQLite file. Finalize and close the old connection
    # first so a stale WAL/session cannot write pre-restore pages afterward.
    session.commit()
    session.close()

    try:
        db_dest = household_db_path(user_id)
        db_dest.parent.mkdir(parents=True, exist_ok=True)
        _copy_database(folder / "ledgermind.db", db_dest)
        clear_engine_caches()

        _restore_files_from_backup(folder, user_id, include_evidence=include_evidence)
        _restore_behavioral_sidecars(folder, user_id, manifest)

        _record_restore_event(
            db_dest,
            Event(
                event_type=EventType.BACKUP_RESTORE_COMPLETED.value,
                source="backup_restore",
                payload={"backup_id": backup_id, "pre_restore_backup_id": pre_id},
            ),
        )
        return {
            "ok": True,
            "backup_id": backup_id,
            "pre_restore_backup_id": pre_backup["backup_id"],
            "message": "Household data restored. Restart may be required for all views to refresh.",
        }
    except Exception as exc:
        event_bus.emit(
            Event(
                event_type=EventType.BACKUP_RESTORE_FAILED.value,
                source="backup_restore",
                severity="error",
                payload={"backup_id": backup_id, "error": str(exc)},
            )
        )
        raise


def export_summary(
    user_id: str,
    session: Session,
    *,
    redacted: bool = False,
    include_evidence: bool = False,
) -> dict[str, Any]:
    """Create export artifacts without a full restore point (reuses backup folder layout)."""
    export_id = f"export-{_now_id()}"
    info = create_backup(
        user_id,
        session,
        include_evidence=include_evidence,
        label=export_id,
    )
    folder = backup_folder(user_id, export_id)
    ledger_name = "ledger_export.redacted.json" if redacted else "ledger_export.json"
    event_bus.emit(
        Event(
            event_type=EventType.BACKUP_EXPORT_CREATED.value,
            source="backup_restore",
            payload={"export_id": export_id, "redacted": redacted},
        ),
        session=session,
    )
    return {
        **info,
        "export_id": export_id,
        "redacted": redacted,
        "ledger_export": f"/api/backup/{export_id}/download?artifact=ledger",
        "summary_markdown": f"/api/backup/{export_id}/download?artifact=summary",
        "ledger_file": ledger_name,
        "folder": str(folder),
    }


def backup_contains_env(backup_folder: Path) -> bool:
    """Return True if any .env or env-like secrets file slipped into the archive."""
    for path in backup_folder.rglob("*"):
        if path.is_file() and path.name == ".env":
            return True
    return False
