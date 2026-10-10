"""AI provider registry and settings API tests."""

import pytest
from fastapi.testclient import TestClient

from app.ai.provider_registry import ProviderConfigurationError, build_ai_settings_payload
from app.ai.providers import get_provider
from app.ai.providers.mock import MockAIProvider
from app.ai.providers.openai_compatible import OpenAICompatibleProvider
from app.config import settings
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_lmstudio_resolves_openai_compatible(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "lmstudio")
    monkeypatch.setattr(settings, "ai_base_url", "http://localhost:1234/v1")
    monkeypatch.setattr(settings, "ai_api_key", "lm-studio")
    provider = get_provider()
    assert isinstance(provider, OpenAICompatibleProvider)


def test_routerbot_resolves_openai_compatible(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "routerbot")
    monkeypatch.setattr(settings, "ai_base_url", "https://router.example/v1")
    monkeypatch.setattr(settings, "ai_api_key", "routerbot")
    provider = get_provider()
    assert isinstance(provider, OpenAICompatibleProvider)


def test_unknown_provider_without_base_url_fails(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "not_a_real_provider")
    monkeypatch.setattr(settings, "ai_base_url", "")
    with pytest.raises(ProviderConfigurationError, match="Unknown AI provider"):
        get_provider()


def test_unknown_provider_with_base_url_uses_openai_compatible(monkeypatch):
    monkeypatch.setattr(settings, "ai_api_key", "local-test-placeholder")
    monkeypatch.setattr(settings, "ai_provider", "custom_gateway")
    monkeypatch.setattr(settings, "ai_base_url", "https://gateway.example/v1")
    provider = get_provider()
    assert isinstance(provider, OpenAICompatibleProvider)


def test_openai_cloud_blocked_unless_allowed(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "openai")
    monkeypatch.setattr(settings, "ai_base_url", "https://api.openai.com/v1")
    monkeypatch.setattr(settings, "ai_api_key", "sk-test-key")
    monkeypatch.setattr(settings, "ai_allow_remote_openai", False)
    with pytest.raises(ProviderConfigurationError, match="Remote OpenAI"):
        get_provider()


def test_openai_cloud_allowed_when_flag_set(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "openai")
    monkeypatch.setattr(settings, "ai_base_url", "https://api.openai.com/v1")
    monkeypatch.setattr(settings, "ai_api_key", "sk-test-key")
    monkeypatch.setattr(settings, "ai_allow_remote_openai", True)
    provider = get_provider()
    assert isinstance(provider, OpenAICompatibleProvider)


def test_mock_provider_offline(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "mock")
    provider = get_provider()
    assert isinstance(provider, MockAIProvider)
    assert provider.health_check()["ok"] is True


def test_settings_payload_never_includes_api_key(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "openai")
    monkeypatch.setattr(settings, "ai_base_url", "https://api.openai.com/v1")
    monkeypatch.setattr(settings, "ai_api_key", "sk-secret-should-not-appear")
    monkeypatch.setattr(settings, "ai_allow_remote_openai", False)
    payload = build_ai_settings_payload(health_check={"ok": False, "error": "blocked"})
    body = str(payload)
    assert "sk-secret-should-not-appear" not in body
    assert payload["api_key_configured"] is True
    assert payload["api_key_preview"] == "sk-s••••pear"
    assert payload["status"] == "misconfigured"


def test_ai_settings_endpoint_returns_warnings(client, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "not_a_real_provider")
    monkeypatch.setattr(settings, "ai_base_url", "")
    res = client.get("/api/settings/ai")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "misconfigured"
    assert data["warnings"]
    assert "sk-" not in str(data)
    assert "api_key" not in data or data.get("api_key") is None


def test_future_cli_provider_not_implemented(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "future_cli_cursor")
    with pytest.raises(ProviderConfigurationError, match="not implemented"):
        get_provider()
