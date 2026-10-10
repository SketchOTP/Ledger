"""Lossless, idempotent migration of legacy travel obligations to misc spending."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session, select

from app.db.models import MiscOccurrence, MiscSpending, TravelExpense, TravelOccurrence


@dataclass
class TravelMigrationResult:
    created: int = 0
    already_migrated: int = 0
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "already_migrated": self.already_migrated,
            "conflicts": list(self.conflicts),
            "errors": list(self.errors),
        }


class TravelMigrationConflict(ValueError):
    """Raised when an existing generic row has the same id but different meaning."""


def _name(expense: TravelExpense) -> str:
    label = (expense.label or "").strip()
    if label:
        return label
    return f"{(expense.mode or 'travel').strip().title()} expense"


def _category(expense: TravelExpense) -> str:
    value = (expense.expense_type or "").strip().lower()
    if value in {"food", "meal", "restaurant", "dining"}:
        return "eating_out"
    if value in {"entertainment", "event", "activity"}:
        return "entertainment"
    return "other"


def _same_expense(source: TravelExpense, target: MiscSpending) -> bool:
    return (
        target.id == source.id
        and target.name == _name(source)
        and target.category == _category(source)
        and float(target.amount) == float(source.amount)
        and target.due_date == source.due_date
        and (target.recurrence or "monthly") == (source.recurrence or "monthly")
        and target.created_at == source.created_at
        and target.updated_at == source.updated_at
    )


def _same_occurrence(source: TravelOccurrence, target: MiscOccurrence) -> bool:
    return (
        target.id == source.id
        and target.misc_spending_id == source.travel_expense_id
        and float(target.amount) == float(source.amount)
        and target.due_date == source.due_date
        and bool(target.paid) == bool(source.paid)
        and target.paid_date == source.paid_date
        and target.created_at == source.created_at
    )


def migrate_travel_to_misc(session: Session, *, dry_run: bool = True) -> TravelMigrationResult:
    """Copy travel economic meaning into generic rows without changing source rows.

    The migration reuses ids, preserves dates/recurrence/payment state, and never
    commits a partial result when an id collision has different semantics.
    """
    result = TravelMigrationResult()
    pending_expenses: list[MiscSpending] = []
    pending_occurrences: list[MiscOccurrence] = []

    for source in session.exec(select(TravelExpense)).all():
        target = session.get(MiscSpending, source.id)
        if target is not None:
            if _same_expense(source, target):
                result.already_migrated += 1
            else:
                result.conflicts.append({"kind": "expense", "id": source.id})
            continue
        pending_expenses.append(
            MiscSpending(
                id=source.id,
                name=_name(source),
                category=_category(source),
                amount=source.amount,
                due_date=source.due_date,
                recurrence=source.recurrence,
                created_at=source.created_at,
                updated_at=source.updated_at,
            )
        )

    for source in session.exec(select(TravelOccurrence)).all():
        target = session.get(MiscOccurrence, source.id)
        if target is not None:
            if _same_occurrence(source, target):
                result.already_migrated += 1
            else:
                result.conflicts.append({"kind": "occurrence", "id": source.id})
            continue
        pending_occurrences.append(
            MiscOccurrence(
                id=source.id,
                misc_spending_id=source.travel_expense_id,
                amount=source.amount,
                due_date=source.due_date,
                paid=source.paid,
                paid_date=source.paid_date,
                created_at=source.created_at,
            )
        )

    result.created = len(pending_expenses) + len(pending_occurrences)
    if result.conflicts:
        if not dry_run:
            raise TravelMigrationConflict(f"Travel migration conflicts: {result.conflicts}")
        return result
    if dry_run:
        return result

    session.add_all(pending_expenses)
    session.add_all(pending_occurrences)
    session.commit()
    return result
