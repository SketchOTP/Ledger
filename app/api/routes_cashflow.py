"""Canonical read-only cashflow and snapshot endpoints.

These endpoints were historically bundled under the Reports router.  They are
shared financial reads, not an advice/report product, so they remain available
under their stable paths without restoring the retired Reports runtime.
"""

import json

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import get_db
from app.core.cashflow_engine import calculate_cashflow
from app.core.cashflow_plan import CASHFLOW_PLAN_HORIZONS, build_cashflow_plan
from app.core.financial_snapshot import build_financial_snapshot

router = APIRouter(prefix="/api", tags=["cashflow"])


@router.get("/cashflow")
def get_cashflow(starting_cash: float | None = None, session: Session = Depends(get_db)):
    return calculate_cashflow(session, starting_cash)


@router.get("/cashflow/plan")
def get_cashflow_plan(days: int = 180, session: Session = Depends(get_db)):
    if days not in CASHFLOW_PLAN_HORIZONS:
        days = 180
    return build_cashflow_plan(session, days)


@router.get("/snapshot")
def get_snapshot(session: Session = Depends(get_db)):
    snapshot = build_financial_snapshot(session)
    return {
        "snapshot": json.loads(snapshot.model_dump_json()),
        "recommendation_count": 0,
        "opportunity_count": 0,
    }
