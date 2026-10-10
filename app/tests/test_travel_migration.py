"""D015 travel-to-misc migration and financial-boundary contracts."""

from datetime import date, datetime

from sqlmodel import Session, select

from app.core.travel_migration import TravelMigrationConflict, migrate_travel_to_misc
from app.db.models import MiscOccurrence, MiscSpending, PaymentArchiveEvent, TravelExpense, TravelOccurrence


def test_travel_migration_is_lossless_and_idempotent(session: Session):
    created_at = datetime(2026, 1, 2, 3, 4, 5)
    updated_at = datetime(2026, 1, 3, 3, 4, 5)
    expense = TravelExpense(
        id="travel-1",
        mode="car",
        expense_type="gas",
        label="Road trip",
        amount=123.45,
        due_date=date(2026, 2, 7),
        recurrence="monthly",
        created_at=created_at,
        updated_at=updated_at,
    )
    occurrence = TravelOccurrence(
        id="travel-occ-1",
        travel_expense_id=expense.id,
        amount=123.45,
        due_date=date(2026, 2, 7),
        paid=True,
        paid_date=date(2026, 2, 8),
        created_at=created_at,
    )
    session.add(expense)
    session.add(occurrence)
    session.add(PaymentArchiveEvent(kind="travel", action="marked_paid", scheduled_date=occurrence.due_date))
    session.commit()

    dry = migrate_travel_to_misc(session, dry_run=True)
    assert dry.created == 2
    assert session.exec(select(MiscSpending)).all() == []

    result = migrate_travel_to_misc(session, dry_run=False)
    assert result.created == 2
    target = session.get(MiscSpending, expense.id)
    assert target is not None
    assert target.amount == expense.amount
    assert target.due_date == expense.due_date
    assert target.recurrence == expense.recurrence
    assert target.created_at == expense.created_at
    target_occurrence = session.get(MiscOccurrence, occurrence.id)
    assert target_occurrence is not None
    assert target_occurrence.misc_spending_id == expense.id
    assert target_occurrence.paid is True
    assert session.exec(select(PaymentArchiveEvent)).all()[0].kind == "travel"

    second = migrate_travel_to_misc(session, dry_run=False)
    assert second.created == 0
    assert second.already_migrated == 2
    assert len(session.exec(select(MiscSpending)).all()) == 1
    assert len(session.exec(select(MiscOccurrence)).all()) == 1


def test_travel_migration_reports_conflicting_ids_without_writes(session: Session):
    expense = TravelExpense(id="same", mode="bus", expense_type="bus_pass", amount=50)
    existing = MiscSpending(id="same", name="Different", category="other", amount=99)
    session.add(expense)
    session.add(existing)
    session.commit()

    dry = migrate_travel_to_misc(session, dry_run=True)
    assert dry.created == 0
    assert dry.conflicts == [{"kind": "expense", "id": "same"}]

    try:
        migrate_travel_to_misc(session, dry_run=False)
    except TravelMigrationConflict:
        pass
    else:
        raise AssertionError("conflicting migration must fail explicitly")
    assert len(session.exec(select(MiscSpending)).all()) == 1
