"""Rate lookup — returns None unless user-supplied; never fabricates."""

from typing import Any


def lookup_rate(product_type: str, user_supplied: dict[str, Any] | None = None) -> float | None:
    if user_supplied and product_type in user_supplied:
        return user_supplied.get(product_type)
    return None
