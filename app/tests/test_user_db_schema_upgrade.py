"""Per-user DB picks up new tables without re-registering."""

from sqlalchemy import text
from sqlmodel import create_engine

from app.db.user_db import ensure_user_db, user_engine


def test_ensure_user_db_creates_travel_tables_on_existing_db(tmp_path, monkeypatch):
    monkeypatch.setattr("app.db.user_db.USERS_DIR", tmp_path / "users")
    user_id = "travel-migrate-test"
    path = tmp_path / "users" / user_id / "ledgermind.db"
    path.parent.mkdir(parents=True)

    # Simulate pre-travel user DB: only onboarding_state
    eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    with eng.connect() as conn:
        conn.execute(text("CREATE TABLE onboarding_state (id TEXT PRIMARY KEY, completed INTEGER DEFAULT 0)"))
        conn.execute(text("INSERT INTO onboarding_state (id, completed) VALUES ('default', 1)"))
        conn.commit()

    ensure_user_db(user_id)

    eng2 = user_engine(user_id)
    with eng2.connect() as conn:
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
    assert "travel_expenses" in tables
    assert "travel_settings" in tables
    assert "opportunity_prediction_snapshots" in tables
    assert "opportunity_outcome_observations" in tables
    assert "opportunity_calibration_audits" in tables


def test_d004_upgrade_adds_research_fingerprint_idempotently(tmp_path):
    from sqlmodel import Session, SQLModel

    from app.db import models  # noqa: F401
    from app.db.models import OpportunityResearchRun
    from app.db.session import apply_sqlite_migrations

    path = tmp_path / "d003.db"
    eng = create_engine(f"sqlite:///{path}")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as session:
        session.add(OpportunityResearchRun(id="keep", trigger="manual"))
        session.commit()
    with eng.connect() as conn:
        conn.execute(text("ALTER TABLE opportunity_research_runs DROP COLUMN material_state_fingerprint"))
        conn.commit()
    apply_sqlite_migrations(eng)
    apply_sqlite_migrations(eng)
    with eng.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(opportunity_research_runs)"))}
        assert "material_state_fingerprint" in columns
        assert conn.execute(text("SELECT id FROM opportunity_research_runs")).scalar_one() == "keep"
