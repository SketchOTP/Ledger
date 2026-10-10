"""Stress forecast API — forward-looking cash exposure (read-only DB)."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session

from app.api.deps import get_db
from app.core.stress_forecast import ALLOWED_HORIZONS, run_stress_forecast

router = APIRouter(prefix="/api/forecast", tags=["forecast"])


class ForecastRunRequest(BaseModel):
    horizon_days: int = 90
    starting_cash_override: float | None = None
    planned_spending_override: float | None = None


@router.get("")
def get_forecast(
    days: int = Query(90, alias="days"),
    session: Session = Depends(get_db),
):
    if days not in ALLOWED_HORIZONS:
        raise HTTPException(
            status_code=400,
            detail=f"days must be one of {sorted(ALLOWED_HORIZONS)}",
        )
    try:
        return run_stress_forecast(session, horizon_days=days)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/run")
def run_forecast(body: ForecastRunRequest, session: Session = Depends(get_db)):
    if body.horizon_days not in ALLOWED_HORIZONS:
        raise HTTPException(
            status_code=400,
            detail=f"horizon_days must be one of {sorted(ALLOWED_HORIZONS)}",
        )
    try:
        return run_stress_forecast(
            session,
            horizon_days=body.horizon_days,
            starting_cash_override=body.starting_cash_override,
            planned_spending_override=body.planned_spending_override,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
