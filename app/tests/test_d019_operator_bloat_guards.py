"""D019 guards for the canonical research and operator boundaries."""

from pathlib import Path

from app.core.scheduler import DEFAULT_JOBS, JOB_RUNNERS, LEGACY_RETIRED_JOBS
from app.main import app

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend" / "src"


def test_legacy_ingestion_is_not_a_scheduler_runtime():
    assert "research_ingestion_check" not in {spec["job_type"] for spec in DEFAULT_JOBS}
    assert "research_ingestion_check" not in JOB_RUNNERS
    assert "research_ingestion_check" in LEGACY_RETIRED_JOBS


def test_legacy_operator_routers_are_not_mounted():
    paths = set(app.openapi()["paths"])
    for prefix in (
        "/api/research/admin",
        "/api/market-data",
        "/api/evidence",
        "/api/evidence-extraction",
        "/api/financial-timeline",
    ):
        assert not any(path.startswith(prefix) for path in paths), prefix
    assert "/api/v2/research/status" in paths
    assert "/api/scheduler/run-due" in paths
    assert "/api/scheduler/jobs" not in paths


def test_operator_shell_contains_only_support_surfaces():
    advanced = (FRONTEND / "pages" / "Advanced.tsx").read_text(encoding="utf-8")
    for label in ("Research administration", "Outcome learning", "Timeline", "Scheduler", "Evidence"):
        assert label not in advanced
    for page in ("ResearchAdmin", "OutcomeLearning", "FinancialTimeline", "Scheduler", "Evidence", "EvidenceReview"):
        assert not (FRONTEND / "pages" / f"{page}.tsx").exists()


def test_retired_operator_urls_are_lightweight_redirects():
    source = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    for path in (
        "/research-admin",
        "/outcome-learning",
        "/financial-timeline",
        "/scheduler",
        "/evidence",
        "/evidence-review",
    ):
        assert f'path="{path}" element={{<Navigate' in source


def test_canonical_v2_research_has_no_legacy_ingestion_import():
    for relative in ("core/v2_intelligence.py", "core/opportunity_research.py", "core/v2_opportunities.py"):
        source = (ROOT / "app" / relative).read_text(encoding="utf-8")
        assert "research.ingestion" not in source
        assert "run_ingestion" not in source
