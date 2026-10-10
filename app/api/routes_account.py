"""Account-level actions (reset household data)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.deps import CurrentUser, get_db
from app.core.household_reset import reset_user_household_files
from app.core.onboarding import assess_onboarding
from app.db.user_db import user_engine

router = APIRouter(prefix="/api/account", tags=["account"])


class ResetHouseholdBody(BaseModel):
    confirm: str = Field(description='Must be exactly "RESET" to wipe all household data')


@router.post("/reset-household")
def reset_household(
    body: ResetHouseholdBody,
    user: CurrentUser,
    _session: Session = Depends(get_db),
):
    if body.confirm.strip() != "RESET":
        return {
            "ok": False,
            "error": 'Type confirm: "RESET" to delete all bills, income, debts, and setup progress.',
        }
    reset_user_household_files(user.id)
    with Session(user_engine(user.id)) as fresh:
        assessment = assess_onboarding(fresh)
    return {
        "ok": True,
        "message": "All household data removed. Setup wizard will start fresh.",
        "onboarding_required": True,
        "completion_score": assessment["completion_score"],
    }
