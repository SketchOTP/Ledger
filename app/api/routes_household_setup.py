"""Household setup status and import templates."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlmodel import Session

from app.api.deps import get_db
from app.core.household_setup import (
    build_household_setup_status,
    list_import_templates,
    template_file_path,
)

router = APIRouter(prefix="/api/household-setup", tags=["household-setup"])


@router.get("")
def household_setup_status(session: Session = Depends(get_db)):
    return build_household_setup_status(session)


@router.get("/templates")
def household_import_templates():
    return {"templates": list_import_templates()}


@router.get("/templates/{template_id}")
def download_import_template(template_id: str):
    path = template_file_path(template_id)
    if not path or not path.is_file():
        raise HTTPException(404, detail="Template not found")
    return FileResponse(
        path,
        media_type="text/csv",
        filename=path.name,
    )
