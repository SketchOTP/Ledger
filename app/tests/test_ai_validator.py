"""AI validation tests."""

from app.ai.validator import validate_cfo_output


def test_invalid_json_rejection():
    result, err = validate_cfo_output('{"executive_summary": "x"}')
    assert result is None
    assert err is not None


def test_valid_output():
    data = {
        "executive_summary": "OK",
        "cash_status": "safe",
        "top_actions": [],
        "risk_flags": [],
        "surplus_strategy": [],
        "debt_strategy": [],
        "research_needed": [],
        "assumptions": [],
        "confidence": 0.8,
    }
    result, err = validate_cfo_output(data)
    assert result is not None
    assert err is None
