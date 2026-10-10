"""Username auth validation."""

import pytest

from app.core.auth import normalize_username, validate_username


def test_normalize_username_lowercase():
    assert normalize_username("Sketch_User") == "sketch_user"


def test_validate_username_ok():
    validate_username("sketch")


def test_validate_username_rejects_short():
    with pytest.raises(ValueError, match="3–32"):
        validate_username("ab")


def test_validate_username_rejects_invalid_chars():
    with pytest.raises(ValueError):
        validate_username("bad name")
