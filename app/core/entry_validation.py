"""Validation for manual financial data entry."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from math import isfinite
from typing import Any


@dataclass
class ValidationResult:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings}


def _err(result: ValidationResult, msg: str) -> None:
    result.ok = False
    result.errors.append(msg)


def _warn(result: ValidationResult, msg: str) -> None:
    result.warnings.append(msg)


def _parse_float(
    value: Any, field_name: str, result: ValidationResult, *, allow_negative: bool = False
) -> float | None:
    if value is None or value == "":
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        _err(result, f"{field_name} must be a number")
        return None
    if not isfinite(n):
        _err(result, f"{field_name} must be a finite number")
        return None
    if n < 0 and not allow_negative:
        _err(result, f"{field_name} cannot be negative")
        return None
    return n


def coerce_date_fields(data: dict[str, Any], *fields: str) -> dict[str, Any]:
    """Convert ISO date strings in API payloads to datetime.date for SQLite."""
    out = dict(data)
    for date_field in fields:
        if date_field not in out:
            continue
        value = out[date_field]
        if value is None or value == "":
            out[date_field] = None
        elif not isinstance(value, date):
            out[date_field] = date.fromisoformat(str(value)[:10])
    return out


def _parse_date(value: Any, field_name: str, result: ValidationResult) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        _err(result, f"{field_name} must be YYYY-MM-DD")
        return None


def validate_cash_balance(data: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    checking = _parse_float(data.get("checking_balance"), "Checking balance", result, allow_negative=True)
    savings = _parse_float(data.get("emergency_fund_balance"), "Savings balance", result)
    _parse_float(data.get("cushion_monthly_contribution"), "Monthly emergency saving", result)
    _parse_float(data.get("target_buffer"), "Cash cushion goal", result)
    _parse_float(data.get("cash_cushion_balance"), "Current cushion balance", result)
    if checking is None and savings is None and not result.errors:
        _err(result, "Enter at least checking or savings balance")
    return result


def validate_paycheck(data: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    if not str(data.get("source_name", "")).strip():
        _err(result, "Paycheck name is required")
    amount = _parse_float(data.get("amount"), "Amount", result)
    if amount is not None and amount <= 0:
        _err(result, "Amount must be greater than zero")
    freq = str(data.get("pay_frequency", "")).strip()
    if not freq:
        _err(result, "Pay frequency is required")
    if not _parse_date(data.get("next_pay_date"), "Next pay date", result):
        _err(result, "Next pay date is required for paycheck scheduling")
    return result


def validate_bill(data: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    if not str(data.get("name", "")).strip():
        _err(result, "Bill name is required")
    amount = _parse_float(data.get("amount"), "Amount", result)
    if amount is not None and amount <= 0:
        _err(result, "Amount must be greater than zero")

    recurrence = str(data.get("recurrence") or "monthly").lower()
    if recurrence == "scheduled":
        payments = data.get("payments") or []
        if not isinstance(payments, list) or not 1 <= len(payments) <= 24:
            _err(result, "Add 1 to 24 dated payments")
            return result
        seen: set[date] = set()
        for row in payments:
            if not isinstance(row, dict):
                _err(result, "Enter a date and amount for each payment")
                continue
            due = _parse_date(row.get("due_date"), "Payment date", result)
            payment_amount = _parse_float(row.get("amount"), "Payment amount", result)
            if payment_amount is None or payment_amount <= 0:
                _err(result, "Each payment amount must be greater than zero")
            if due in seen:
                _err(result, "Each payment needs its own date")
            if due is not None:
                seen.add(due)
        return result
    if recurrence not in {"weekly", "biweekly", "monthly", "quarterly", "annual", "once", "none"}:
        _err(result, "Choose a supported payment frequency")

    repeats = data.get("repeats", True)
    if repeats is False or recurrence in ("once", "none"):
        repeats = False
    else:
        repeats = True

    due = _parse_date(data.get("due_date"), "Due date", result)
    if repeats and due is None and not any("Due date" in e for e in result.errors):
        _err(result, "Due date is required for repeating bills")
    return result


def validate_debt(data: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    if not str(data.get("creditor", "")).strip():
        _err(result, "Creditor name is required")
    balance = _parse_float(data.get("balance"), "Balance", result)
    apr = _parse_float(data.get("apr"), "APR", result)
    minimum = _parse_float(data.get("minimum_payment"), "Minimum payment", result)

    if apr is not None and apr > 100:
        _err(result, "APR cannot be over 100%")
    if apr is not None and apr <= 0:
        _err(result, "APR must be greater than zero")

    if balance is not None and minimum is not None and minimum > balance:
        _warn(result, "Minimum payment is higher than balance — double-check both amounts")

    return result


def validate_credit_card(data: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    if not str(data.get("issuer", "")).strip() and not str(data.get("card_name", "")).strip():
        _err(result, "Issuer or card name is required")

    balance = _parse_float(data.get("balance"), "Balance", result)
    limit = _parse_float(data.get("credit_limit"), "Credit limit", result)
    apr = _parse_float(data.get("apr"), "APR", result)
    minimum = _parse_float(data.get("minimum_payment"), "Minimum payment", result)

    if apr is not None and apr > 100:
        _err(result, "APR cannot be over 100%")
    if apr is not None and apr <= 0:
        _err(result, "APR must be greater than zero")

    if balance is not None and limit is not None and balance > limit:
        _warn(result, "Balance is over the credit limit — verify balance and limit")

    if balance is not None and minimum is not None and minimum > balance:
        _warn(result, "Minimum payment exceeds balance")

    return result


def validate_misc_spending(data: dict[str, Any]) -> ValidationResult:
    from app.core.bill_schedule import bill_repeats
    from app.core.misc_catalog import MISC_CATEGORIES

    result = ValidationResult()
    name = str(data.get("name", "")).strip()
    if not name:
        _err(result, "Name is required")
    category = str(data.get("category", "other")).lower().strip()
    if category not in MISC_CATEGORIES:
        _err(result, "Category must be daily_spending, eating_out, entertainment, or other")
    amount = _parse_float(data.get("amount"), "Amount", result)
    if amount is not None and amount <= 0:
        _err(result, "Amount must be greater than zero")
    recurrence = str(data.get("recurrence") or "monthly").lower()
    if data.get("repeats") is False:
        recurrence = "once"
    if bill_repeats(recurrence):
        if _parse_date(data.get("due_date"), "Due date", result) is None and not any(
            "Due date" in e for e in result.errors
        ):
            _err(result, "Due date is required for repeating expenses")
    return result


def validate_rent(data: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    rent = _parse_float(data.get("monthly_rent"), "Monthly rent", result)
    if rent is not None and rent <= 0:
        _err(result, "Monthly rent must be greater than zero")
    due = data.get("rent_due_date")
    if not due:
        _err(result, "Rent due date is required")
    return result


def normalize_mortgage_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Map due_by / late_by dates and keep legacy start_date in sync."""
    out = coerce_date_fields(dict(data), "start_date", "due_by_date", "late_by_date")
    if not out.get("due_by_date") and out.get("start_date"):
        out["due_by_date"] = out["start_date"]
    if out.get("due_by_date"):
        out["start_date"] = out["due_by_date"]
    return out


def validate_mortgage(data: dict[str, Any]) -> ValidationResult:
    from app.core.bill_schedule import bill_repeats

    result = ValidationResult()
    if not str(data.get("lender", "")).strip():
        _err(result, "Lender name is required")

    rate = _parse_float(data.get("interest_rate"), "Interest rate", result)
    if rate is None and not any("Interest rate" in e for e in result.errors):
        _err(result, "Interest rate is required")
    elif rate is not None and (rate <= 0 or rate > 20):
        _err(result, "Interest rate should be between 0 and 20%")

    balance = _parse_float(data.get("current_balance"), "Current balance", result)
    payment = _parse_float(data.get("monthly_payment"), "Monthly payment", result)
    if payment is not None and payment <= 0:
        _err(result, "Monthly payment must be greater than zero")

    value = _parse_float(data.get("property_value_estimate"), "Property value", result)
    if value is None and not any("Property" in e for e in result.errors):
        _warn(result, "Property value helps mortgage optimization — add when known")

    if balance is not None and payment is not None and payment > balance:
        _warn(result, "Monthly payment is higher than balance — verify amounts")

    recurrence = str(data.get("recurrence") or "monthly").lower()
    if bill_repeats(recurrence):
        due = _parse_date(
            data.get("due_by_date") or data.get("start_date"),
            "Due by date",
            result,
        )
        late = _parse_date(data.get("late_by_date"), "Late by date", result)
        if due is None and not any("Due by" in e for e in result.errors):
            _err(result, "Due by date is required for repeating mortgage payments")
        if isinstance(due, date) and isinstance(late, date) and late < due:
            _err(result, "Late by date must be on or after due by date")
        elif isinstance(due, date) and isinstance(late, date) and (late - due).days > 31:
            _warn(result, "Grace period over 31 days — confirm late by date")

    return result


def raise_http_if_invalid(result: ValidationResult) -> None:
    from fastapi import HTTPException

    if not result.ok:
        raise HTTPException(
            status_code=422,
            detail={"message": "Validation failed", "errors": result.errors, "warnings": result.warnings},
        )
