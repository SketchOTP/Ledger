"""Data entry: validation, CSV import/export, fix queue, My Money summary."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlmodel import Session

from app.api.deps import get_db
from app.core.csv_io import SUPPORTED_KINDS, export_csv, import_csv
from app.core.data_fix_queue import build_data_fix_queue
from app.core.entry_validation import (
    validate_bill,
    validate_cash_balance,
    validate_credit_card,
    validate_debt,
    validate_mortgage,
    validate_paycheck,
    validate_rent,
)
from app.core.my_money_summary import build_my_money_summary

router = APIRouter(tags=["data-entry"])


class CsvImportBody(BaseModel):
    content: str


class ValidateBody(BaseModel):
    kind: str
    data: dict


@router.get("/api/my-money/summary")
def my_money_summary(session: Session = Depends(get_db)):
    return build_my_money_summary(session)


@router.get("/api/data-fixes")
def data_fixes(session: Session = Depends(get_db)):
    return build_data_fix_queue(session)


@router.post("/api/validate")
def validate_entry(body: ValidateBody):
    validators = {
        "paycheck": validate_paycheck,
        "bill": validate_bill,
        "debt": validate_debt,
        "credit_card": validate_credit_card,
        "mortgage": validate_mortgage,
        "rent": validate_rent,
        "cash_balance": validate_cash_balance,
    }
    fn = validators.get(body.kind)
    if not fn:
        raise HTTPException(400, f"Unknown kind: {body.kind}")
    return fn(body.data).to_dict()


@router.get("/api/export/{kind}")
def export_data_csv(kind: str, session: Session = Depends(get_db)):
    if kind not in SUPPORTED_KINDS:
        raise HTTPException(400, f"Unsupported export: {kind}")
    content = export_csv(session, kind)
    return PlainTextResponse(
        content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{kind}.csv"'},
    )


@router.get("/api/export/{kind}/headers")
def export_headers(kind: str):
    from app.core.csv_io import EXPORT_HEADERS

    if kind not in EXPORT_HEADERS:
        raise HTTPException(400, f"Unsupported kind: {kind}")
    return {"kind": kind, "headers": EXPORT_HEADERS[kind]}


@router.post("/api/import/{kind}")
def import_data_csv(kind: str, body: CsvImportBody, session: Session = Depends(get_db)):
    if kind not in SUPPORTED_KINDS:
        raise HTTPException(400, f"Unsupported import: {kind}")
    try:
        return import_csv(session, kind, body.content)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
