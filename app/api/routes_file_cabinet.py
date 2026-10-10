"""Legacy File Cabinet archive/recovery API.

The V2 product no longer provides general document management. This router only
preserves recovery for existing owner data and historical download links.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlmodel import Session

from app.api.deps import AUTH_DISABLED, CurrentUser, get_db
from app.core.file_cabinet import (
    archive_status,
    build_archive_manifest,
    create_archive_export,
    remove_temporary_archive,
    resolve_document_path,
)

router = APIRouter(prefix="/api/file-cabinet", tags=["legacy-file-cabinet"])


def _user_id(user: CurrentUser) -> str:
    return user.id if not AUTH_DISABLED else "dev-local"


@router.get("/status")
def get_status(user: CurrentUser, session: Session = Depends(get_db)):
    return archive_status(session, _user_id(user))


@router.get("/documents")
def get_documents(user: CurrentUser, session: Session = Depends(get_db)):
    """Return legacy inventory metadata only; archive inspection is read-only."""
    return build_archive_manifest(session, _user_id(user))


@router.get("/documents/{doc_id}/download")
def download_document(doc_id: str, user: CurrentUser, session: Session = Depends(get_db)):
    path = resolve_document_path(session, _user_id(user), doc_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Archived file not found")
    return FileResponse(path, filename=path.name)


@router.post("/export")
def export_archive(
    background_tasks: BackgroundTasks,
    user: CurrentUser,
    session: Session = Depends(get_db),
):
    path = create_archive_export(session, _user_id(user))
    background_tasks.add_task(remove_temporary_archive, path)
    return FileResponse(path, filename="file-cabinet-archive.zip", media_type="application/zip")
