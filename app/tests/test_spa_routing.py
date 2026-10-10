"""SPA index.html fallback for deep links and Tailscale path stripping."""

from fastapi.testclient import TestClient


def test_spa_fallback_under_public_base():
    from app.main import FRONTEND_DIST, PUBLIC_BASE, app

    if not FRONTEND_DIST.exists() or not PUBLIC_BASE:
        return

    client = TestClient(app)
    for path in (f"{PUBLIC_BASE}/money", f"{PUBLIC_BASE}/login", f"{PUBLIC_BASE}/register"):
        res = client.get(path)
        assert res.status_code == 200, path
        assert "text/html" in res.headers.get("content-type", "")
        assert 'id="root"' in res.text


def test_spa_fallback_for_tailscale_stripped_paths():
    """tailscale serve --set-path=/lm forwards /lm/foo as /foo to the local app."""
    from app.main import FRONTEND_DIST, app

    if not FRONTEND_DIST.exists():
        return

    client = TestClient(app)
    for path in ("/money", "/login", "/onboarding"):
        res = client.get(path)
        assert res.status_code == 200, path
        assert "text/html" in res.headers.get("content-type", "")
        assert 'id="root"' in res.text


def test_missing_asset_still_404():
    from app.main import FRONTEND_DIST, PUBLIC_BASE, app

    if not FRONTEND_DIST.exists():
        return

    client = TestClient(app)
    base = PUBLIC_BASE or ""
    res = client.get(f"{base}/assets/does-not-exist.js")
    assert res.status_code == 404
