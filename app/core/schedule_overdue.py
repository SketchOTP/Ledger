"""Shared past-due anchor helpers for recurring obligation schedules."""

from __future__ import annotations

from datetime import date


def overdue_anchor_amount(
    anchor: date | None,
    period_start: date,
    amount: float,
    *,
    is_paid: bool,
) -> float:
    """Count an unpaid obligation whose schedule anchor is before period_start."""
    if anchor is None or anchor >= period_start or amount <= 0 or is_paid:
        return 0.0
    return amount


def anchor_overdue_tuple(
    anchor: date | None,
    today: date,
    amount: float,
    *,
    is_paid: bool,
) -> tuple[date, float] | None:
    if anchor is None or anchor >= today or amount <= 0 or is_paid:
        return None
    return (anchor, amount)
