"""Additive V2 capability and opportunity substrate API."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.api.deps import get_db
from app.core.household_advisor import build_advisory
from app.core.opportunity_research import (
    SCOPES,
    list_research_runs,
    provider_yield_scorecard,
    research_status,
    run_opportunity_research,
)
from app.core.v2_opportunities import (
    accept_candidate_action,
    add_asset,
    add_skill,
    candidate_applicability,
    candidate_history,
    candidate_payload,
    candidate_summary,
    create_candidate_action,
    decision_pool,
    dismiss_candidate,
    ingest_candidate,
    opportunity_quality_summary,
    profile_payload,
    qualify_candidate,
    ranked_candidates,
    realized_value_summary,
    refresh_household_decision_dependents,
    respond_to_recurring_candidate,
    top_moves,
    transition_candidate,
    update_asset,
    update_profile,
    update_skill,
    validation_bottleneck_summary,
)
from app.core.v2_outcome_learning import learning_payload, record_candidate_outcome
from app.core.v2_today import build_today
from app.core.v2_validation import save_fact, validation_payload
from app.db.models import OpportunityCandidate, OpportunityOutcomeObservation, OpportunityPredictionSnapshot

router = APIRouter(prefix="/api/v2", tags=["v2-opportunities"])


class ValidationFactInput(BaseModel):
    fact_type: str = Field(min_length=1, max_length=80)
    value: Any
    units: str = ""
    source_type: str = "owner_entered"
    source_label: str = ""
    observed_at: datetime | None = None
    expires_at: datetime | None = None
    verification_status: str = "owner_entered"


class RecurringResponseInput(BaseModel):
    response: str = Field(min_length=1, max_length=30)
    new_recurring_amount: float | None = Field(default=None, ge=0)
    replacement_cadence: str | None = Field(default=None, max_length=20)
    cancellation_fee: float | None = Field(default=None, ge=0)


@router.get("/today")
def today(session: Session = Depends(get_db)) -> dict[str, Any]:
    return build_today(session)


@router.get("/capability-profile")
def read_profile(session: Session = Depends(get_db)) -> dict[str, Any]:
    return profile_payload(session)


@router.put("/capability-profile")
def write_profile(body: dict[str, Any], session: Session = Depends(get_db)) -> dict[str, Any]:
    result = update_profile(session, body)
    refresh_household_decision_dependents(session, {"capability"})
    return result


@router.post("/capability-profile/skills")
def create_skill(body: dict[str, Any], session: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        result = add_skill(session, body).model_dump()
        refresh_household_decision_dependents(session, {"capability"})
        return result
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.put("/capability-profile/skills/{skill_id}")
def edit_skill(skill_id: str, body: dict[str, Any], session: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        result = update_skill(session, skill_id, body).model_dump()
        refresh_household_decision_dependents(session, {"capability"})
        return result
    except ValueError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.post("/capability-profile/assets")
def create_asset(body: dict[str, Any], session: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        result = add_asset(session, body).model_dump()
        refresh_household_decision_dependents(session, {"capability"})
        return result
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.put("/capability-profile/assets/{asset_id}")
def edit_asset(asset_id: str, body: dict[str, Any], session: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        result = update_asset(session, asset_id, body).model_dump()
        refresh_household_decision_dependents(session, {"capability"})
        return result
    except ValueError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.get("/candidates")
def list_candidates(session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [candidate_payload(row) for row in session.exec(select(OpportunityCandidate)).all()]


@router.post("/candidates")
def create_candidate(body: dict[str, Any], session: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return candidate_payload(ingest_candidate(session, body))
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.get("/candidates/{candidate_id}")
def read_candidate(candidate_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    row = session.get(OpportunityCandidate, candidate_id)
    if not row:
        raise HTTPException(404)
    payload = candidate_payload(row)
    payload["applicability"] = candidate_applicability(session, row)
    return payload


@router.get("/candidates/{candidate_id}/validation")
def candidate_validation(candidate_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    row = session.get(OpportunityCandidate, candidate_id)
    if not row:
        raise HTTPException(404)
    return validation_payload(session, row)


@router.put("/candidates/{candidate_id}/validation")
def write_candidate_validation(
    candidate_id: str, body: ValidationFactInput, session: Session = Depends(get_db)
) -> dict[str, Any]:
    row = session.get(OpportunityCandidate, candidate_id)
    if not row:
        raise HTTPException(404)
    try:
        return save_fact(
            session,
            row,
            fact_type=body.fact_type,
            value=body.value,
            units=body.units,
            source_type=body.source_type,
            source_label=body.source_label,
            observed_at=body.observed_at,
            expires_at=body.expires_at,
            verification_status=body.verification_status,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.post("/candidates/{candidate_id}/recurring-response")
def recurring_response(
    candidate_id: str, body: RecurringResponseInput, session: Session = Depends(get_db)
) -> dict[str, Any]:
    row = session.get(OpportunityCandidate, candidate_id)
    if not row:
        raise HTTPException(404)
    try:
        return candidate_payload(
            respond_to_recurring_candidate(
                session,
                row,
                body.response,
                new_recurring_amount=body.new_recurring_amount,
                replacement_cadence=body.replacement_cadence,
                cancellation_fee=body.cancellation_fee,
            )
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.post("/candidates/{candidate_id}/qualify")
def qualify(candidate_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    row = session.get(OpportunityCandidate, candidate_id)
    if not row:
        raise HTTPException(404)
    return qualify_candidate(session, row)


@router.post("/candidates/{candidate_id}/transition/{target}")
def transition(
    candidate_id: str, target: str, body: dict[str, Any] | None = None, session: Session = Depends(get_db)
) -> dict[str, Any]:
    row = session.get(OpportunityCandidate, candidate_id)
    if not row:
        raise HTTPException(404)
    try:
        return candidate_payload(transition_candidate(session, row, target, (body or {}).get("reason", "")))
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.get("/moves")
def moves(session: Session = Depends(get_db)) -> dict[str, Any]:
    current = decision_pool(session)
    advisory = build_advisory(session, generate_hypotheses=False)
    return {
        "ranked": ranked_candidates(session),
        "top_moves": top_moves(session),
        "candidates": [
            {**candidate_summary(row), "applicability": candidate_applicability(session, row)} for row in current
        ],
        "history": [candidate_summary(row) for row in candidate_history(session)],
        "quality": opportunity_quality_summary(session),
        "validation": validation_bottleneck_summary(session),
        "value": realized_value_summary(session),
        "advisory": advisory,
    }


@router.get("/opportunity-quality")
def opportunity_quality(session: Session = Depends(get_db)) -> dict[str, Any]:
    return opportunity_quality_summary(session)


@router.get("/value-realization")
def value_realization(session: Session = Depends(get_db)) -> dict[str, Any]:
    return realized_value_summary(session)


@router.post("/candidates/{candidate_id}/action")
def candidate_action(candidate_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    row = session.get(OpportunityCandidate, candidate_id)
    if not row:
        raise HTTPException(404)
    try:
        action = create_candidate_action(session, row)
        return {column.name: getattr(action, column.name) for column in action.__table__.columns}
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.post("/candidates/{candidate_id}/accept")
def accept_candidate(candidate_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    row = session.get(OpportunityCandidate, candidate_id)
    if not row:
        raise HTTPException(404)
    try:
        action = accept_candidate_action(session, row)
        return {
            "candidate": candidate_payload(row),
            "action": {column.name: getattr(action, column.name) for column in action.__table__.columns},
        }
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.post("/candidates/{candidate_id}/dismiss")
def dismiss(
    candidate_id: str, body: dict[str, Any], session: Session = Depends(get_db)
) -> dict[str, Any]:
    row = session.get(OpportunityCandidate, candidate_id)
    if not row:
        raise HTTPException(404)
    try:
        return candidate_payload(
            dismiss_candidate(session, row, str(body.get("reason_code") or ""), str(body.get("note") or ""))
        )
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.get("/candidates/{candidate_id}/outcome")
def candidate_outcome(candidate_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    candidate = session.get(OpportunityCandidate, candidate_id)
    if not candidate:
        raise HTTPException(404)
    snapshot = session.exec(
        select(OpportunityPredictionSnapshot).where(OpportunityPredictionSnapshot.candidate_id == candidate_id)
    ).first()
    observation = session.exec(
        select(OpportunityOutcomeObservation).where(OpportunityOutcomeObservation.candidate_id == candidate_id)
    ).first()
    def row_payload(row: Any) -> dict[str, Any] | None:
        return ({col.name: getattr(row, col.name) for col in row.__table__.columns} if row else None)
    return {"prediction": row_payload(snapshot), "outcome": row_payload(observation)}


@router.put("/candidates/{candidate_id}/outcome")
def write_candidate_outcome(
    candidate_id: str, body: dict[str, Any], session: Session = Depends(get_db)
) -> dict[str, Any]:
    candidate = session.get(OpportunityCandidate, candidate_id)
    if not candidate:
        raise HTTPException(404)
    try:
        row = record_candidate_outcome(session, candidate, body)
        return {col.name: getattr(row, col.name) for col in row.__table__.columns}
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.get("/learning")
def v2_learning(session: Session = Depends(get_db)) -> dict[str, Any]:
    return learning_payload(session)


@router.post("/research/refresh")
def refresh_research(body: dict[str, Any] | None = None, session: Session = Depends(get_db)) -> dict[str, Any]:
    scope = str((body or {}).get("scope") or "all")
    if scope not in SCOPES:
        raise HTTPException(400, detail=f"scope must be one of {sorted(SCOPES)}")
    return run_opportunity_research(session, scope=scope, trigger="manual")


@router.get("/research/runs")
def research_runs(session: Session = Depends(get_db)) -> dict[str, Any]:
    return {"runs": list_research_runs(session)}


@router.get("/research/status")
def research_refresh_status(session: Session = Depends(get_db)) -> dict[str, Any]:
    return research_status(session)


@router.get("/research/provider-yield")
def research_provider_yield(session: Session = Depends(get_db)) -> dict[str, Any]:
    return {
        "cohort": "post_d021r1_canonical",
        "providers": provider_yield_scorecard(session),
        "validation": validation_bottleneck_summary(session),
    }
