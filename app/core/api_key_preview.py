"""Truncated API key previews for settings UI (never return full secrets)."""


def api_key_preview(key: str | None) -> str | None:
    """Return a truncated preview or None when unset."""
    value = (key or "").strip()
    if not value:
        return None
    if len(value) <= 8:
        return value[:2] + "••••"
    return value[:4] + "••••" + value[-4:]
