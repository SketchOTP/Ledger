"""Auth password length rules."""

import pytest

from app.core.auth import MIN_PASSWORD_LENGTH, SESSION_DAYS_BROWSER, SESSION_DAYS_REMEMBER, validate_password_length


def test_pin_length_allowed():
    validate_password_length("1234")
    validate_password_length("abcd")


def test_password_too_short():
    with pytest.raises(ValueError, match=str(MIN_PASSWORD_LENGTH)):
        validate_password_length("123")


def test_remember_me_longer_session():
    assert SESSION_DAYS_REMEMBER > SESSION_DAYS_BROWSER
