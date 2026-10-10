"""Household resilience and wealth-advisor API."""

from __future__ import annotations

from typing import Any, Type

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.api.deps import get_db
from app.core.household_advisor import build_advisory, persist_advisory
from app.db.models import (
    HouseholdCoverage,
    HouseholdIncomeRisk,
    HouseholdLifeEvent,
    HouseholdMember,
    HouseholdProfile,
    HouseholdRisk,
    HouseholdVehicle,
)

router = APIRouter(prefix="/api/v2", tags=["household-advisory"])


def _dump(row: Any) -> dict[str, Any]:
    return row.model_dump(mode="json")


def _profile(session: Session) -> HouseholdProfile:
    row = session.exec(select(HouseholdProfile)).first()
    if row is None:
        row = HouseholdProfile()
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


@router.get("/household")
def household(session: Session = Depends(get_db)) -> dict[str, Any]:
    profile = _profile(session)
    return {
        "profile": _dump(profile),
        "risks": [_dump(row) for row in session.exec(select(HouseholdRisk)).all()],
        "life_events": [_dump(row) for row in session.exec(select(HouseholdLifeEvent)).all()],
        "coverage": [_dump(row) for row in session.exec(select(HouseholdCoverage)).all()],
        "vehicles": [_dump(row) for row in session.exec(select(HouseholdVehicle)).all()],
        "members": [_dump(row) for row in session.exec(select(HouseholdMember)).all()],
        "income_risks": [_dump(row) for row in session.exec(select(HouseholdIncomeRisk)).all()],
    }


@router.put("/household")
def update_household(body: dict[str, Any], session: Session = Depends(get_db)) -> dict[str, Any]:
    row = _profile(session)
    allowed = {
        "risk_tolerance",
        "target_reserve_months",
        "desired_growth_share",
        "desired_free_share",
        "dependents_count",
        "income_notes",
        "planning_notes",
        "dedicated_resilience_balance",
    }
    for key, value in body.items():
        if key in allowed:
            setattr(row, key, value)
    session.add(row)
    session.commit()
    session.refresh(row)
    return _dump(row)


def _create_or_update(
    session: Session,
    model: Type[Any],
    body: dict[str, Any],
    row_id: str | None = None,
) -> dict[str, Any]:
    row = session.get(model, row_id) if row_id else None
    if row_id and row is None:
        raise HTTPException(404, detail="We couldn't find that household detail.")
    if row is None:
        row = model(profile_id="default", **{key: value for key, value in body.items() if key != "id"})
    else:
        for key, value in body.items():
            if key not in {"id", "profile_id", "created_at"} and hasattr(row, key):
                setattr(row, key, value)
    session.add(row)
    session.commit()
    session.refresh(row)
    return _dump(row)


@router.post("/household/risks")
def add_risk(body: dict[str, Any], session: Session = Depends(get_db)) -> dict[str, Any]:
    return _create_or_update(session, HouseholdRisk, body)


@router.put("/household/risks/{row_id}")
def edit_risk(row_id: str, body: dict[str, Any], session: Session = Depends(get_db)) -> dict[str, Any]:
    return _create_or_update(session, HouseholdRisk, body, row_id)


@router.post("/household/life-events")
def add_life_event(body: dict[str, Any], session: Session = Depends(get_db)) -> dict[str, Any]:
    return _create_or_update(session, HouseholdLifeEvent, body)


@router.put("/household/life-events/{row_id}")
def edit_life_event(row_id: str, body: dict[str, Any], session: Session = Depends(get_db)) -> dict[str, Any]:
    return _create_or_update(session, HouseholdLifeEvent, body, row_id)


@router.post("/household/coverage")
def add_coverage(body: dict[str, Any], session: Session = Depends(get_db)) -> dict[str, Any]:
    return _create_or_update(session, HouseholdCoverage, body)


@router.post("/household/vehicles")
def add_vehicle(body: dict[str, Any], session: Session = Depends(get_db)) -> dict[str, Any]:
    return _create_or_update(session, HouseholdVehicle, body)


@router.put("/household/vehicles/{row_id}")
def edit_vehicle(row_id: str, body: dict[str, Any], session: Session = Depends(get_db)) -> dict[str, Any]:
    return _create_or_update(session, HouseholdVehicle, body, row_id)


@router.post("/household/members")
def add_member(body: dict[str, Any], session: Session = Depends(get_db)) -> dict[str, Any]:
    return _create_or_update(session, HouseholdMember, body)


@router.put("/household/members/{row_id}")
def edit_member(row_id: str, body: dict[str, Any], session: Session = Depends(get_db)) -> dict[str, Any]:
    return _create_or_update(session, HouseholdMember, body, row_id)


@router.post("/household/income-risks")
def add_income_risk(body: dict[str, Any], session: Session = Depends(get_db)) -> dict[str, Any]:
    return _create_or_update(session, HouseholdIncomeRisk, body)


@router.put("/household/income-risks/{row_id}")
def edit_income_risk(row_id: str, body: dict[str, Any], session: Session = Depends(get_db)) -> dict[str, Any]:
    return _create_or_update(session, HouseholdIncomeRisk, body, row_id)


@router.put("/household/coverage/{row_id}")
def edit_coverage(row_id: str, body: dict[str, Any], session: Session = Depends(get_db)) -> dict[str, Any]:
    return _create_or_update(session, HouseholdCoverage, body, row_id)


@router.delete("/household/coverage/{row_id}")
def delete_coverage(row_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    row = session.get(HouseholdCoverage, row_id)
    if row is None:
        raise HTTPException(404, detail="We couldn't find that household detail.")
    session.delete(row)
    session.commit()
    return {"id": row_id, "removed": True}


@router.delete("/household/vehicles/{row_id}")
def delete_vehicle(row_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    row = session.get(HouseholdVehicle, row_id)
    if row is None:
        raise HTTPException(404, detail="We couldn't find that household detail.")
    session.delete(row)
    session.commit()
    return {"id": row_id, "removed": True}


@router.delete("/household/life-events/{row_id}")
def delete_life_event(row_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    row = session.get(HouseholdLifeEvent, row_id)
    if row is None:
        raise HTTPException(404, detail="We couldn't find that household detail.")
    session.delete(row)
    session.commit()
    return {"id": row_id, "removed": True}


def _deactivate(model: Type[Any], row_id: str, session: Session) -> dict[str, Any]:
    row = session.get(model, row_id)
    if row is None:
        raise HTTPException(404, detail="We couldn't find that household detail.")
    if hasattr(row, "active"):
        row.active = False
    session.add(row)
    session.commit()
    return _dump(row)


@router.delete("/household/risks/{row_id}")
def delete_risk(row_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    return _deactivate(HouseholdRisk, row_id, session)


@router.delete("/household/members/{row_id}")
def delete_member(row_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    return _deactivate(HouseholdMember, row_id, session)


@router.delete("/household/income-risks/{row_id}")
def delete_income_risk(row_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    return _deactivate(HouseholdIncomeRisk, row_id, session)


@router.get("/advisory")
def advisory(session: Session = Depends(get_db)) -> dict[str, Any]:
    return build_advisory(session, generate_hypotheses=False)


@router.post("/advisory/refresh")
def refresh_advisory(session: Session = Depends(get_db)) -> dict[str, Any]:
    result = build_advisory(session, generate_hypotheses=True)
    persist_advisory(session, result)
    return result
