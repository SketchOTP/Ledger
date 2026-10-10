"""D018 guards for the consolidated V2 product shell."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend" / "src"


def test_money_owns_financial_entry_and_legacy_routes_redirect():
    app = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    for section, route in (
        ("bills", "/bills"),
        ("debts", "/debts"),
        ("mortgage", "/mortgage"),
        ("credit_cards", "/credit-cards"),
    ):
        assert f'<Route path="{route}" element={{<Navigate to="/money?section={section}" replace />}} />' in app
    for page in ("Bills", "Debts", "Mortgage", "CreditCards"):
        assert f'./pages/{page}' not in app
        assert not (FRONTEND / "pages" / f"{page}.tsx").exists()


def test_parallel_shell_surfaces_are_removed_or_redirected():
    app = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    assert '<Route path="/more" element={<Navigate to="/advanced" replace />} />' in app
    assert '<Route path="/data-fixes" element={<Navigate to="/data-quality" replace />} />' in app
    assert '<Route path="/household-setup" element={<Navigate to="/money" replace />} />' in app
    assert '<Route path="/events" element={<Navigate to="/advanced" replace />} />' in app
    for page in ("More", "DataFixes", "HouseholdSetup", "Events"):
        assert f'./pages/{page}' not in app
        assert not (FRONTEND / "pages" / f"{page}.tsx").exists()
    assert not (FRONTEND / "lib" / "moreNav.ts").exists()
    assert not (FRONTEND / "lib" / "appMode.ts").exists()
    assert not (FRONTEND / "theme" / "AppModeContext.tsx").exists()


def test_data_quality_absorbs_actionable_fix_queue():
    page = (FRONTEND / "pages" / "DataQuality.tsx").read_text(encoding="utf-8")
    assert "api.dataFixes()" in page
    assert "Actionable fixes" in page
    assert "item.fix_path" in page


def test_settings_owns_account_and_theme_controls():
    settings = (FRONTEND / "pages" / "Settings.tsx").read_text(encoding="utf-8")
    assert "useAuth" in settings
    assert "useTheme" in settings
    assert "resetHousehold" in settings
    assert "handleLogout" in settings
