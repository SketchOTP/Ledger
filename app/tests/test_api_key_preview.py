"""API key preview truncation."""

from app.core.api_key_preview import api_key_preview


def test_api_key_preview_unset():
    assert api_key_preview("") is None
    assert api_key_preview(None) is None
    assert api_key_preview("   ") is None


def test_api_key_preview_short():
    assert api_key_preview("abc") == "ab••••"
    assert api_key_preview("12345678") == "12••••"


def test_api_key_preview_long_truncates_ends():
    full = "sk-secret-should-not-appear"
    preview = api_key_preview(full)
    assert preview is not None
    assert full not in preview
    assert preview.startswith("sk-s")
    assert preview.endswith("pear")
    assert "••••" in preview
