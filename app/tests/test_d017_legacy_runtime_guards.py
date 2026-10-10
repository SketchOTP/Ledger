"""D017 guards: retired V1 advice APIs stay absent while V2 remains mounted."""

from pathlib import Path

from app.main import app

ROOT = Path(__file__).resolve().parents[2]


def test_retired_runtime_modules_and_routers_are_absent():
    for relative in (
        "api/routes_action_plan.py",
        "api/routes_behavioral.py",
        "api/routes_command_center.py",
        "api/routes_daily_briefing.py",
        "api/routes_dashboard.py",
        "api/routes_recommendations.py",
        "api/routes_reports.py",
        "core/action_plan.py",
        "core/command_center.py",
        "core/daily_briefing.py",
        "core/dashboard.py",
        "core/report_engine.py",
        "core/recommendation_engine.py",
        "core/recommendation_scoring.py",
        "core/outcome_learning.py",
        "intelligence/financial_health.py",
    ):
        assert not (ROOT / relative).exists(), relative


def test_openapi_excludes_retired_advice_surfaces_and_keeps_v2_contract():
    paths = set(app.openapi()["paths"])
    retired_prefixes = (
        "/api/daily-briefing",
        "/api/reports",
        "/api/recommendations",
        "/api/actions",
        "/api/command-center",
        "/api/dashboard",
        "/api/financial-health",
        "/api/life-goals",
        "/api/spending-alignment",
        "/api/human-capital",
        "/api/risk-checklist",
        "/api/rent-vs-own",
        "/api/outcome-learning",
    )
    assert not any(path.startswith(prefix) for path in paths for prefix in retired_prefixes)
    for required in (
        "/api/v2/today",
        "/api/v2/moves",
        "/api/cashflow",
        "/api/forecast",
    ):
        assert required in paths


def test_main_does_not_mount_retired_router_modules():
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    for name in (
        "routes_action_plan",
        "routes_behavioral",
        "routes_command_center",
        "routes_daily_briefing",
        "routes_dashboard",
        "routes_recommendations",
        "routes_reports",
    ):
        assert name not in source


def test_no_new_legacy_advice_job_defaults():
    scheduler = (ROOT / "app" / "core" / "scheduler.py").read_text(encoding="utf-8")
    assert '"daily_briefing"' not in scheduler.split("DEFAULT_JOB_SPECS", 1)[-1].split("LEGACY_RETIRED_JOBS", 1)[0]
    assert '"cfo_report"' not in scheduler.split("DEFAULT_JOB_SPECS", 1)[-1].split("LEGACY_RETIRED_JOBS", 1)[0]
