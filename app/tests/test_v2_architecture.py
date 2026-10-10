"""Regression guards for the D006 V2-to-legacy dependency boundary."""

from pathlib import Path

from app.core.obligation_attention import build_obligation_attention


def test_v2_today_does_not_import_command_center_composition():
    source = (Path(__file__).parents[1] / "core" / "v2_today.py").read_text(encoding="utf-8")
    assert "app.core.command_center" not in source
    assert "_build_quick_status" not in source


def test_shared_obligation_attention_remains_canonical(session, sample_data):
    payload = build_obligation_attention(session, window_days=14)
    assert isinstance(payload, list)
