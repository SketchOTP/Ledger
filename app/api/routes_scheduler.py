"""Operational scheduler trigger used by the installed service loop.

The household scheduler UI and mutation endpoints are retired. This narrow
endpoint remains because the user-systemd scheduler invokes it locally.
"""

import threading

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import CurrentUser, get_db
from app.api.routes_settings import _user_id_for_ai
from app.core.scheduler import run_due_jobs

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])
_run_due_lock = threading.Lock()


@router.post("/run-due")
def api_run_due(user: CurrentUser, session: Session = Depends(get_db)):
    """Run due canonical jobs for the authenticated scheduler owner."""
    user_id = _user_id_for_ai(user)
    if not _run_due_lock.acquire(blocking=False):
        return {"ran": 0, "results": [], "skipped": "overlap"}
    try:
        return run_due_jobs(session, user_id=user_id)
    finally:
        _run_due_lock.release()
