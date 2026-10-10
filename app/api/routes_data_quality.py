"""Data quality API — trust layer for CFO recommendations."""

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import get_db
from app.core.data_quality import assess_data_quality
from app.core.income_aggregate import sync_borrower_income_from_sources

router = APIRouter(prefix="/api/data-quality", tags=["data-quality"])


@router.get("")
def get_data_quality(session: Session = Depends(get_db)):
    """Assess data quality across all financial domains."""
    sync_borrower_income_from_sources(session)
    return assess_data_quality(session)
