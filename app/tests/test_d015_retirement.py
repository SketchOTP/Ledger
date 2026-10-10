"""D015 guards for retired product boundaries and generic spending economics."""

from datetime import date, timedelta
from pathlib import Path

from app.core.liquidity_planner import _collect_events
from app.core.obligation_schedule import obligation_events_in_range
from app.core.travel_migration import migrate_travel_to_misc
from app.db.models import MiscOccurrence, MiscSpending, TravelExpense, TravelOccurrence


def test_retired_grocery_and_travel_surfaces_are_not_mounted():
    from app.main import app

    paths = {getattr(route, "path", "") for route in app.routes}
    assert not any(path.startswith("/api/groceries") for path in paths)
    assert not any(path.startswith("/api/travel") for path in paths)

    frontend = Path(__file__).parents[2] / "frontend" / "src"
    assert not (frontend / "pages" / "Groceries.tsx").exists()
    assert not (frontend / "pages" / "GroceryOptimizer.tsx").exists()
    assert not (frontend / "pages" / "GroceryDealOptimizer.tsx").exists()
    assert not (frontend / "components" / "money" / "TravelSection.tsx").exists()


def test_migrated_travel_obligation_uses_generic_literal_date_and_paid_state(session):
    today = date.today()
    source = TravelExpense(
        id="travel-economic",
        mode="plane",
        expense_type="airfare",
        label="Dated trip",
        amount=321.09,
        due_date=today + timedelta(days=8),
        recurrence="once",
    )
    paid = TravelOccurrence(
        id="travel-paid",
        travel_expense_id=source.id,
        amount=321.09,
        due_date=source.due_date,
        paid=True,
        paid_date=today,
    )
    session.add(source)
    session.add(paid)
    session.commit()

    result = migrate_travel_to_misc(session, dry_run=False)
    assert result.created == 2

    # The paid occurrence suppresses the corresponding generic obligation.
    start = today
    end = today + timedelta(days=30)
    rows = obligation_events_in_range(session, start, end)
    assert not any(row["due_date"] == source.due_date.isoformat() for row in rows)

    # An unpaid generic occurrence remains a literal-date obligation and is
    # consumed by the same canonical event collector as all other misc spend.
    generic = session.get(TravelExpense, source.id)
    assert generic is not None  # source travel row remains historical
    misc_occ = session.get(MiscOccurrence, paid.id)
    assert misc_occ is not None and misc_occ.paid is True
    session.add(MiscSpending(
        id="generic-unpaid",
        name="Generic dated spending",
        category="other",
        amount=321.09,
        due_date=today + timedelta(days=10),
        recurrence="once",
    ))
    session.add(
        MiscOccurrence(
            id="travel-unpaid",
            misc_spending_id="generic-unpaid",
            amount=321.09,
            due_date=today + timedelta(days=10),
            paid=False,
        )
    )
    session.commit()
    events, _ = _collect_events(session, end)
    assert not any(event.ref_id == source.id and event.category == "travel" for event in events)
    assert any(event.ref_id == "generic-unpaid" and event.category == "misc" for event in events)
