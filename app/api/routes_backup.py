"""Backup, restore, and portable export API."""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.deps import CurrentUser, get_db
from app.api.routes_settings import _user_id_for_ai
from app.core.backup_restore import (
    backup_folder,
    backup_zip_path,
    create_backup,
    export_summary,
    list_backups,
    restore_backup,
)

router = APIRouter(prefix="/api/backup", tags=["backup"])


class BackupCreateBody(BaseModel):
    include_evidence: bool = False


class BackupRestoreBody(BaseModel):
    backup_id: str = Field(..., min_length=1)
    confirm: str = Field(..., min_length=1)


class ExportSummaryBody(BaseModel):
    redacted: bool = False
    include_evidence: bool = False


@router.post("/create")
def api_create_backup(body: BackupCreateBody, user: CurrentUser, session: Session = Depends(get_db)):
    user_id = _user_id_for_ai(user)
    try:
        return create_backup(user_id, session, include_evidence=body.include_evidence)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/restore")
def api_restore_backup(body: BackupRestoreBody, user: CurrentUser, session: Session = Depends(get_db)):
    user_id = _user_id_for_ai(user)
    try:
        return restore_backup(user_id, body.backup_id, session, confirm=body.confirm)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/list")
def api_list_backups(user: CurrentUser):
    user_id = _user_id_for_ai(user)
    return {"backups": list_backups(user_id)}


@router.get("/{backup_id}/download")
def api_download_backup(
    backup_id: str,
    user: CurrentUser,
    artifact: str = Query("zip", description="zip | ledger | summary"),
    redacted: bool = Query(False, description="Use redacted ledger export when artifact=ledger"),
):
    user_id = _user_id_for_ai(user)
    try:
        if artifact == "zip":
            path = backup_zip_path(user_id, backup_id)
            return FileResponse(
                path,
                media_type="application/zip",
                filename=f"ledger-backup-{backup_id}.zip",
            )
        folder = backup_folder(user_id, backup_id)
        if artifact == "ledger":
            if redacted:
                candidate = folder / "ledger_export.redacted.json"
                filename = "ledger_export.redacted.json"
            else:
                candidate = folder / "ledger_export.json"
                filename = "ledger_export.json"
            if candidate.is_file():
                return FileResponse(candidate, media_type="application/json", filename=filename)
            raise HTTPException(status_code=404, detail="Ledger export not found")
        if artifact == "summary":
            path = folder / "human_readable_summary.md"
            if not path.is_file():
                raise HTTPException(status_code=404, detail="Summary not found")
            return FileResponse(path, media_type="text/markdown", filename="household-summary.md")
        raise HTTPException(status_code=400, detail=f"Unknown artifact: {artifact}")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/export-summary")
def api_export_summary(
    body: ExportSummaryBody,
    user: CurrentUser,
    session: Session = Depends(get_db),
):
    user_id = _user_id_for_ai(user)
    try:
        return export_summary(
            user_id,
            session,
            redacted=body.redacted,
            include_evidence=body.include_evidence,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
