"""Onboarding wizard API."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from app.api.deps import get_db
from app.core.onboarding import (
    assess_onboarding,
    complete_onboarding,
    get_declarations,
    mark_step_completed,
    start_onboarding,
    update_declarations,
)
from app.events.bus import event_bus
from app.events.types import Event, EventType

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


class DeclarationsUpdate(BaseModel):
    no_mortgage: bool | None = None
    no_credit_cards: bool | None = None
    no_debts: bool | None = None
    no_bills: bool | None = None
    skip_grocery: bool | None = None
    research_acknowledged: bool | None = None


@router.get("")
def get_onboarding_status(session: Session = Depends(get_db)):
    return assess_onboarding(session)


@router.post("/start")
def onboarding_start(session: Session = Depends(get_db)):
    result = start_onboarding(session)
    event_bus.emit(
        Event(
            event_type=EventType.ONBOARDING_STARTED.value,
            source="onboarding",
            payload={},
        ),
        session=session,
    )
    session.commit()
    return result


@router.post("/step/{step_id}")
def onboarding_step_completed(step_id: str, session: Session = Depends(get_db)):
    mark_step_completed(session, step_id)
    result = assess_onboarding(session)
    event_bus.emit(
        Event(
            event_type=EventType.ONBOARDING_STEP_COMPLETED.value,
            source="onboarding",
            payload={"step_id": step_id, "completion_score": result["completion_score"]},
        ),
        session=session,
    )
    session.commit()
    return result


@router.post("/complete")
def onboarding_complete(session: Session = Depends(get_db)):
    result = complete_onboarding(session)
    event_bus.emit(
        Event(
            event_type=EventType.ONBOARDING_COMPLETED.value,
            source="onboarding",
            payload={
                "completion_score": result["completion_score"],
                "status": result["status"],
            },
        ),
        session=session,
    )
    session.commit()
    return result


@router.patch("/declarations")
def patch_declarations(body: DeclarationsUpdate, session: Session = Depends(get_db)):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    update_declarations(session, updates)
    return assess_onboarding(session)


@router.get("/declarations")
def get_onboarding_declarations(session: Session = Depends(get_db)):
    return get_declarations(session)
