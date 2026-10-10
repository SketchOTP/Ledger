"""Database session management."""

import sqlite3
from collections.abc import Generator

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    """Make every SQLite connection tolerate concurrency.

    Without these, SQLite's default busy_timeout is 0, so any concurrent
    write contention (per-request migrations + parallel frontend calls)
    immediately raises "database is locked" -> HTTP 500. WAL lets readers
    and a writer proceed together; busy_timeout makes contenders wait
    instead of failing. Registered globally so it covers the main, per-user,
    master, and test engines alike. No-op for non-SQLite backends.
    """
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


connect_args = {"check_same_thread": False} if settings.is_sqlite else {}
engine = create_engine(settings.database_url, echo=False, connect_args=connect_args)


def _sqlite_migrate_grocery_cart_run_items(eng) -> None:
    from sqlalchemy import text

    with eng.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(grocery_cart_run_items)")).fetchall()
        if not rows:
            return
        cols = {r[1] for r in rows}
        if "alternative_candidates_json" not in cols:
            conn.execute(
                text("ALTER TABLE grocery_cart_run_items ADD COLUMN alternative_candidates_json TEXT DEFAULT '[]'")
            )
        conn.commit()


def apply_sqlite_migrations(eng) -> None:
    """Add columns introduced after first deploy (safe to run repeatedly)."""
    if not settings.is_sqlite:
        return
    _sqlite_migrate_recommendations(eng)
    _sqlite_migrate_borrower_profile(eng)
    _sqlite_migrate_borrower_rent(eng)
    _sqlite_migrate_payment_recurrence(eng)
    _sqlite_migrate_mortgage_due_dates(eng)
    _sqlite_migrate_financial_offers(eng)
    _sqlite_migrate_payment_archive(eng)
    _sqlite_migrate_grocery_cart_run_items(eng)
    _sqlite_migrate_grocery_cart_runs(eng)
    _sqlite_migrate_financial_actions_v2(eng)
    _sqlite_migrate_opportunity_candidates_v2(eng)
    _sqlite_migrate_opportunity_research_v4(eng)
    _sqlite_migrate_liquid_account_yield(eng)
    _sqlite_migrate_validation_facts(eng)
    _sqlite_migrate_household_advisory(eng)
    _sqlite_migrate_flexible_payments(eng)
    _sqlite_migrate_cash_cushion(eng)
    _sqlite_migrate_budget_weekday(eng)
    _sqlite_migrate_recorded_spending(eng)
    _sqlite_migrate_checking_confirmation(eng)


def _sqlite_migrate_checking_confirmation(eng) -> None:
    """Baseline existing payment history; only new marks need cash reconfirmation."""
    from sqlalchemy import text

    with eng.connect() as conn:
        present = {row[1] for row in conn.execute(text("PRAGMA table_info(borrower_profile)"))}
        if present and "checking_confirmed_at" not in present:
            conn.execute(text("ALTER TABLE borrower_profile ADD COLUMN checking_confirmed_at DATETIME"))
            conn.execute(text("UPDATE borrower_profile SET checking_confirmed_at = CURRENT_TIMESTAMP"))
            conn.commit()


def _sqlite_migrate_recorded_spending(eng) -> None:
    """Link manually logged purchases to the planned day without moving cash."""
    from sqlalchemy import text

    with eng.connect() as conn:
        present = {row[1] for row in conn.execute(text("PRAGMA table_info(transactions)"))}
        if not present:
            return
        additions = {
            "planned_budget_category_id": "TEXT",
            "planned_spending_date": "DATE",
            "reflected_in_checking": "BOOLEAN NOT NULL DEFAULT 0",
        }
        for column, definition in additions.items():
            if column not in present:
                conn.execute(text(f"ALTER TABLE transactions ADD COLUMN {column} {definition}"))
        conn.commit()


def _sqlite_migrate_budget_weekday(eng) -> None:
    """Give existing weekly plans an explicit, editable shopping day."""
    from sqlalchemy import text

    with eng.connect() as conn:
        present = {row[1] for row in conn.execute(text("PRAGMA table_info(budget_categories)"))}
        if present and "planned_weekday" not in present:
            conn.execute(text("ALTER TABLE budget_categories ADD COLUMN planned_weekday INTEGER DEFAULT 5"))
            conn.commit()


def _sqlite_migrate_cash_cushion(eng) -> None:
    """Keep legacy target-only profiles conservative until an actual balance is entered."""
    from sqlalchemy import text

    with eng.connect() as conn:
        present = {row[1] for row in conn.execute(text("PRAGMA table_info(borrower_profile)"))}
        for column, definition in {
            "cushion_monthly_contribution": "REAL NOT NULL DEFAULT 100",
            "pay_period_allowance_enabled": "BOOLEAN NOT NULL DEFAULT 0",
        }.items():
            if present and column not in present:
                conn.execute(text(f"ALTER TABLE borrower_profile ADD COLUMN {column} {definition}"))
        conn.commit()
        if present and "cash_cushion_balance" not in present:
            conn.execute(text("ALTER TABLE borrower_profile ADD COLUMN cash_cushion_balance REAL"))
            conn.commit()


def _sqlite_migrate_flexible_payments(eng) -> None:
    """Preserve old monthly plans while allowing dated bill installments."""
    from sqlalchemy import text

    additions = {
        "bills": {"schedule_json": "TEXT NOT NULL DEFAULT '[]'"},
        "budget_categories": {
            "usual_amount": "REAL",
            "period": "TEXT NOT NULL DEFAULT 'month'",
        },
        "misc_spending": {"active": "BOOLEAN NOT NULL DEFAULT 1"},
    }
    with eng.connect() as conn:
        for table, columns in additions.items():
            present = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            if not present:
                continue
            for name, definition in columns.items():
                if name not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
        conn.commit()


def _sqlite_migrate_household_advisory(eng) -> None:
    """Add ENH-001 household facts without rewriting existing household data."""
    from sqlalchemy import text

    from app.db.models import HouseholdIncomeRisk, HouseholdMember

    additions = {
        "household_profiles": {"dedicated_resilience_balance": "REAL DEFAULT 0"},
        "household_risks": {
            "likelihood_band": "TEXT DEFAULT 'unknown'",
            "impact_low": "REAL",
            "impact_high": "REAL",
            "income_impact_percent": "REAL",
            "horizon_months": "INTEGER",
            "mitigation": "TEXT DEFAULT ''",
            "provenance": "TEXT DEFAULT 'owner_entered'",
            "confidence": "TEXT DEFAULT 'unknown'",
        },
        "household_life_events": {
            "estimated_cost_low": "REAL",
            "estimated_cost_high": "REAL",
            "income_impact": "REAL",
            "duration_months": "INTEGER",
        },
        "household_coverages": {
            "deductible": "REAL",
            "out_of_pocket_max": "REAL",
            "income_replacement_percent": "REAL",
            "waiting_period_days": "INTEGER",
            "coverage_gap": "REAL",
        },
        "household_vehicles": {
            "year": "INTEGER",
            "make": "TEXT DEFAULT ''",
            "model": "TEXT DEFAULT ''",
            "mileage": "INTEGER",
            "required_for_income": "BOOLEAN DEFAULT 0",
            "alternate_transport": "BOOLEAN DEFAULT 0",
            "backup_vehicle": "BOOLEAN DEFAULT 0",
            "warranty": "BOOLEAN DEFAULT 0",
            "deductible": "REAL",
            "repair_exposure_low": "REAL",
            "repair_exposure_high": "REAL",
        },
    }
    with eng.connect() as conn:
        for table, cols_to_add in additions.items():
            rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            if not rows:
                continue
            cols = {row[1] for row in rows}
            for name, definition in cols_to_add.items():
                if name not in cols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
        HouseholdMember.__table__.create(eng, checkfirst=True)
        HouseholdIncomeRisk.__table__.create(eng, checkfirst=True)
        conn.commit()


def _sqlite_migrate_liquid_account_yield(eng) -> None:
    """Add optional account-yield facts without changing existing balances."""
    from sqlalchemy import text

    with eng.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(liquid_accounts)")).fetchall()
        if not rows:
            return
        cols = {row[1] for row in rows}
        if "current_apy" not in cols:
            conn.execute(text("ALTER TABLE liquid_accounts ADD COLUMN current_apy REAL"))
        if "apy_as_of" not in cols:
            conn.execute(text("ALTER TABLE liquid_accounts ADD COLUMN apy_as_of DATE"))
        conn.commit()


def _sqlite_migrate_validation_facts(eng) -> None:
    """Create the additive, versioned candidate-fact table on old SQLite DBs."""
    from app.db.models import OpportunityValidationFact

    OpportunityValidationFact.__table__.create(eng, checkfirst=True)


def _sqlite_migrate_grocery_cart_runs(eng) -> None:
    from sqlalchemy import text

    with eng.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(grocery_cart_runs)")).fetchall()
        if not rows:
            return
        cols = {r[1] for r in rows}
        if "scan_batch_id" not in cols:
            conn.execute(text("ALTER TABLE grocery_cart_runs ADD COLUMN scan_batch_id TEXT DEFAULT ''"))
        if "scan_recommendation" not in cols:
            conn.execute(text("ALTER TABLE grocery_cart_runs ADD COLUMN scan_recommendation TEXT DEFAULT ''"))
        conn.commit()


def _sqlite_migrate_financial_actions_v2(eng) -> None:
    """Add the V2 candidate link without rewriting existing action history."""
    from sqlalchemy import text

    with eng.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(financial_actions)")).fetchall()
        if not rows:
            return
        cols = {row[1] for row in rows}
        if "opportunity_candidate_id" not in cols:
            conn.execute(text("ALTER TABLE financial_actions ADD COLUMN opportunity_candidate_id TEXT"))
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_financial_actions_opportunity_candidate_id "
                "ON financial_actions (opportunity_candidate_id)"
            )
        )
        conn.commit()


def _sqlite_migrate_opportunity_candidates_v2(eng) -> None:
    """Keep early D002 databases additive as the candidate contract grows."""
    from sqlalchemy import text

    additions = {
        "required_activities_json": "TEXT DEFAULT '[]'",
        "experiment_json": "TEXT DEFAULT '{}'",
        "actual_net_value": "REAL",
        "actual_setup_hours": "REAL",
        "actual_recurring_hours_per_week": "REAL",
        "actual_time_to_value_days": "INTEGER",
        "actual_capital_lost": "REAL",
        "actual_capital_returned": "REAL",
        "outcome_failure_reason": "TEXT DEFAULT ''",
        "outcome_notes": "TEXT DEFAULT ''",
    }
    with eng.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(opportunity_candidates)")).fetchall()
        if not rows:
            return
        cols = {row[1] for row in rows}
        for name, definition in additions.items():
            if name not in cols:
                conn.execute(text(f"ALTER TABLE opportunity_candidates ADD COLUMN {name} {definition}"))
        conn.commit()


def _sqlite_migrate_opportunity_research_v4(eng) -> None:
    """Add D004's non-destructive material-change marker to D003 runs."""
    from sqlalchemy import text

    with eng.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(opportunity_research_runs)")).fetchall()
        if not rows:
            return
        cols = {row[1] for row in rows}
        if "material_state_fingerprint" not in cols:
            conn.execute(
                text("ALTER TABLE opportunity_research_runs ADD COLUMN material_state_fingerprint TEXT DEFAULT ''")
            )
        conn.commit()


def _sqlite_migrate_payment_archive(eng) -> None:
    from app.db.models import PaymentArchiveEvent

    PaymentArchiveEvent.__table__.create(eng, checkfirst=True)


def _sqlite_migrate_financial_offers(eng) -> None:
    from sqlalchemy import text

    with eng.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(financial_offers)")).fetchall()
        if not rows:
            return
        cols = {r[1] for r in rows}
        additions = {
            "offer_name": "TEXT DEFAULT ''",
            "apy": "REAL",
            "term_months": "INTEGER",
            "balance_transfer_fee_percent": "REAL",
            "annual_fee": "REAL DEFAULT 0",
            "origination_fee": "REAL",
            "closing_costs": "REAL",
            "evidence_notes": "TEXT DEFAULT ''",
            "expiration_date": "DATE",
            "requires_credit_check": "INTEGER DEFAULT 1",
            "notes": "TEXT DEFAULT ''",
            "linked_account_id": "TEXT",
            "linked_account_type": "TEXT",
            "intake_source": "TEXT DEFAULT 'research'",
            "intake_status": "TEXT DEFAULT 'approved'",
            "comparison_json": "TEXT DEFAULT '{}'",
            "updated_at": "TEXT",
        }
        for col, typedef in additions.items():
            if col not in cols:
                conn.execute(text(f"ALTER TABLE financial_offers ADD COLUMN {col} {typedef}"))
        conn.commit()


def init_db() -> None:
    """Create all tables and apply lightweight SQLite column migrations."""
    SQLModel.metadata.create_all(engine)
    apply_sqlite_migrations(engine)


def _sqlite_migrate_recommendations(eng) -> None:
    from sqlalchemy import text

    with eng.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(recommendations)")).fetchall()
        cols = {r[1] for r in rows}
        if "audit_json" not in cols:
            conn.execute(text("ALTER TABLE recommendations ADD COLUMN audit_json TEXT DEFAULT '{}'"))
        if "scores_json" not in cols:
            conn.execute(text("ALTER TABLE recommendations ADD COLUMN scores_json TEXT DEFAULT '{}'"))
        conn.commit()


def _sqlite_migrate_borrower_profile(eng) -> None:
    from sqlalchemy import text

    with eng.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(borrower_profile)")).fetchall()
        cols = {r[1] for r in rows}
        if "checking_balance" not in cols:
            conn.execute(text("ALTER TABLE borrower_profile ADD COLUMN checking_balance REAL DEFAULT 0"))
        if "housing_status" not in cols:
            conn.execute(text("ALTER TABLE borrower_profile ADD COLUMN housing_status TEXT DEFAULT 'rent'"))
        conn.commit()


def _sqlite_migrate_borrower_rent(eng) -> None:
    from sqlalchemy import text

    with eng.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(borrower_profile)")).fetchall()
        cols = {r[1] for r in rows}
        if "monthly_rent" not in cols:
            conn.execute(text("ALTER TABLE borrower_profile ADD COLUMN monthly_rent REAL DEFAULT 0"))
        if "rent_due_date" not in cols:
            conn.execute(text("ALTER TABLE borrower_profile ADD COLUMN rent_due_date DATE"))
        conn.commit()


def _sqlite_migrate_payment_recurrence(eng) -> None:
    from sqlalchemy import text

    with eng.connect() as conn:
        for table in ("debts", "mortgage_accounts"):
            rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            cols = {r[1] for r in rows}
            if "recurrence" not in cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN recurrence TEXT DEFAULT 'monthly'"))
        rows = conn.execute(text("PRAGMA table_info(credit_cards)")).fetchall()
        cols = {r[1] for r in rows}
        if "recurrence" not in cols:
            conn.execute(text("ALTER TABLE credit_cards ADD COLUMN recurrence TEXT DEFAULT 'monthly'"))
        conn.commit()


def _sqlite_migrate_mortgage_due_dates(eng) -> None:
    from sqlalchemy import text

    with eng.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(mortgage_accounts)")).fetchall()
        cols = {r[1] for r in rows}
        if "due_by_date" not in cols:
            conn.execute(text("ALTER TABLE mortgage_accounts ADD COLUMN due_by_date DATE"))
        if "late_by_date" not in cols:
            conn.execute(text("ALTER TABLE mortgage_accounts ADD COLUMN late_by_date DATE"))
        conn.execute(
            text(
                "UPDATE mortgage_accounts SET due_by_date = start_date "
                "WHERE due_by_date IS NULL AND start_date IS NOT NULL"
            )
        )
        conn.commit()


def get_session() -> Generator[Session, None, None]:
    from app.events.bus import event_bus

    with Session(engine) as session:
        event_bus.set_session(session)
        try:
            yield session
        finally:
            event_bus.set_session(None)
