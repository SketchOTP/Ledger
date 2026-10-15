"""Ledger personal finance application."""

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.deps import AUTH_DISABLED
from app.api.routes_account import router as account_router
from app.api.routes_advisory import router as advisory_router
from app.api.routes_ai import router as ai_router
from app.api.routes_auth import router as auth_router
from app.api.routes_backup import router as backup_router
from app.api.routes_bills import router as bills_router
from app.api.routes_cashflow import router as cashflow_router
from app.api.routes_data_entry import router as data_entry_router
from app.api.routes_data_quality import router as data_quality_router
from app.api.routes_debts import router as debts_router
from app.api.routes_events import router as events_router
from app.api.routes_file_cabinet import router as file_cabinet_router
from app.api.routes_forecast import router as forecast_router
from app.api.routes_goals import router as goals_router
from app.api.routes_household_setup import router as household_setup_router
from app.api.routes_income import router as income_router
from app.api.routes_misc import router as misc_router
from app.api.routes_notifications import router as notifications_router
from app.api.routes_obligations import router as obligations_router
from app.api.routes_onboarding import router as onboarding_router
from app.api.routes_payment_archive import router as payment_archive_router
from app.api.routes_profile import router as profile_router
from app.api.routes_push import router as push_router
from app.api.routes_research_settings import router as research_settings_router
from app.api.routes_scheduler import router as scheduler_router
from app.api.routes_settings import router as settings_router
from app.api.routes_spending import router as spending_router
from app.api.routes_v2_opportunities import router as v2_opportunities_router
from app.config import ensure_data_dirs, settings
from app.core.user_ai_settings import clear_user_ai_settings
from app.db.session import init_db
from app.db.user_db import init_master_db
from app.events.bus import event_bus
from app.events.handlers import register_default_handlers
from app.static_spa import SPAStaticFiles

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

_raw_public_base = os.getenv("LEDGERMIND_PUBLIC_BASE", "").strip()
PUBLIC_BASE = (
    ""
    if _raw_public_base in ("", "/")
    else f"/{_raw_public_base.strip('/')}"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_data_dirs()
    init_master_db()
    _validation = os.getenv("LEDGERMIND_VALIDATION", "").lower() in ("1", "true", "yes")
    production = not _validation and (
        os.getenv("LEDGERMIND_MODE", "").lower() == "production" or FRONTEND_DIST.exists()
    )
    if production and AUTH_DISABLED:
        raise RuntimeError(
            "LEDGERMIND_AUTH_DISABLED cannot be used in production. "
            "Remove it from .env so each login uses data/users/<user-id>/ledgermind.db."
        )
    # Legacy single-user DB only when auth is disabled (tests / dev).
    if AUTH_DISABLED:
        init_db()
    register_default_handlers(event_bus)
    yield


app = FastAPI(title="Ledger", version="1.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class UserAIContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        finally:
            clear_user_ai_settings()


class NoCacheHtmlMiddleware(BaseHTTPMiddleware):
    """Ensure index.html is not cached so SPA asset hashes stay in sync after deploy."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if "text/html" in (response.headers.get("content-type") or ""):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response


app.add_middleware(NoCacheHtmlMiddleware)
app.add_middleware(UserAIContextMiddleware)


@app.exception_handler(OperationalError)
async def database_operational_error_handler(_request: Request, exc: OperationalError) -> JSONResponse:
    """Surface SQLite contention as a retryable error instead of a blank 500."""
    message = str(getattr(exc, "orig", exc))
    locked = "database is locked" in message.lower()
    return JSONResponse(
        status_code=503 if locked else 500,
        content={
            "detail": (
                "Database is temporarily busy (concurrent scheduler or API writes). "
                "Retry in a few seconds."
                if locked
                else "Database operation failed."
            ),
            "error_type": "database_operational",
            "message": message,
        },
    )


_API_ROUTERS = (
    auth_router,
    account_router,
    income_router,
    bills_router,
    cashflow_router,
    obligations_router,
    payment_archive_router,
    debts_router,
    events_router,
    profile_router,
    spending_router,
    misc_router,
    goals_router,
    ai_router,
    settings_router,
    research_settings_router,
    scheduler_router,
    file_cabinet_router,
    onboarding_router,
    forecast_router,
    data_quality_router,
    data_entry_router,
    household_setup_router,
    backup_router,
    notifications_router,
    push_router,
    v2_opportunities_router,
    advisory_router,
)


def _mount_api_routers(prefix: str = "") -> None:
    for router in _API_ROUTERS:
        app.include_router(router, prefix=prefix)


_mount_api_routers()
if PUBLIC_BASE:
    _mount_api_routers(prefix=PUBLIC_BASE)


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "ledgermind-cfo"}


if PUBLIC_BASE:

    @app.get(f"{PUBLIC_BASE}/api/health")
    def health_public_base():
        return {"status": "ok", "service": "ledgermind-cfo", "public_base": PUBLIC_BASE}


if FRONTEND_DIST.exists():
    _spa_static = SPAStaticFiles(directory=str(FRONTEND_DIST), html=True)
    if PUBLIC_BASE:
        app.mount(
            PUBLIC_BASE,
            _spa_static,
            name="static_public",
        )
    app.mount("/", _spa_static, name="static")


def run_doctor() -> int:
    """CLI doctor: verify environment and DB."""
    print("Ledger — Doctor")
    errors = []
    print(f"[INFO] Code root: {Path(__file__).resolve().parent.parent}")
    print(f"[INFO] Persistent state root: {settings.data_dir}")
    print(f"[INFO] Frontend assets: {FRONTEND_DIST}")
    try:
        ensure_data_dirs()
        from app.db.user_db import MASTER_DB_PATH, USERS_DIR

        USERS_DIR.mkdir(parents=True, exist_ok=True)
        for label, path in (
            ("State root", settings.data_dir),
            ("Master DB", MASTER_DB_PATH),
            ("Users directory", USERS_DIR),
        ):
            parent = path if path.suffix == "" else path.parent
            if not parent.exists() or not os.access(parent, os.W_OK):
                raise OSError(f"{label} parent is not writable: {parent}")
            print(f"[OK] {label}: {path}")
        if FRONTEND_DIST.is_dir():
            print("[OK] Frontend assets remain code-root files")
        else:
            print("[WARN] Frontend assets are not built")
    except Exception as e:
        errors.append(f"Paths: {e}")
        print(f"[FAIL] Paths: {e}")
    try:
        init_db()
        print("[OK] Database initialized")
    except Exception as e:
        errors.append(f"Database: {e}")
        print(f"[FAIL] Database: {e}")

    try:
        from app.core.cashflow_engine import calculate_cashflow
        from app.db.session import Session, engine

        with Session(engine) as session:
            cf = calculate_cashflow(session)
            print(f"[OK] Cashflow engine: risk={cf['risk_level']}")
    except Exception as e:
        errors.append(f"Cashflow: {e}")
        print(f"[FAIL] Cashflow: {e}")

    try:
        from app.ai.provider_registry import ProviderConfigurationError
        from app.ai.providers import get_provider

        h = get_provider().health_check()
        if h.get("ok"):
            print(f"[OK] AI provider: {settings.ai_provider}")
        else:
            print(f"[WARN] AI provider unavailable: {h.get('error', 'unknown')}")
    except ProviderConfigurationError as e:
        print(f"[FAIL] AI misconfigured: {e}")
        errors.append(f"AI: {e}")
    except Exception as e:
        print(f"[WARN] AI: {e}")

    if errors:
        print(f"\nDoctor finished with {len(errors)} error(s)")
        return 1
    print("\nDoctor: all checks passed")
    return 0


def main() -> None:
    import uvicorn

    os.environ.setdefault("AI_PROVIDER", "mock")
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=os.getenv("LEDGERMIND_RELOAD", "0") == "1",
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        from app.db.session import Session, engine  # noqa: F401

        sys.exit(run_doctor())
    main()
