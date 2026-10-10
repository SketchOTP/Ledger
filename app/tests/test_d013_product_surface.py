"""D013 guards for the canonical V2 frontend product boundary."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend" / "src"


def test_primary_navigation_has_no_competing_advice_surfaces():
    advanced = (FRONTEND / "pages" / "Advanced.tsx").read_text(encoding="utf-8")
    for label in ("Recommendations", "Action plan", "Reports"):
        assert f'"{label}"' not in advanced
    assert "Outcome learning" not in advanced


def test_retired_frontend_pages_are_not_eagerly_or_lazily_mounted():
    app = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    for page in ("Home", "DailyBriefing", "Recommendations", "ActionPlan", "Opportunities", "CfoOverview"):
        assert f'./pages/{page}' not in app
    assert '<Route path="/reports" element={<Navigate to="/advanced" replace />} />' in app


def test_reports_surface_is_retired_redirect():
    app = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    assert 'path="/reports" element={<Navigate to="/advanced" replace />}' in app
    assert not (FRONTEND / "pages" / "Reports.tsx").exists()


def test_retired_frontend_implementation_files_are_removed():
    for name in ("Home", "DailyBriefing", "Recommendations", "ActionPlan", "Opportunities", "CfoOverview"):
        assert not (FRONTEND / "pages" / f"{name}.tsx").exists()
    assert not (FRONTEND / "components" / "home").exists()
