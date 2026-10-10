"""Per-user database initialization."""

import shutil

from app.db.user_db import ensure_user_db, init_user_db, user_db_path


def _cleanup(path) -> None:
    """Remove the per-user dir, including WAL/SHM sidecar files."""
    shutil.rmtree(path.parent, ignore_errors=True)


def test_ensure_user_db_repairs_zero_byte_file():
    user_id = "test-zero-byte-user"
    path = user_db_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    ensure_user_db(user_id)
    assert path.stat().st_size > 0
    from sqlalchemy import text

    from app.db.user_db import user_engine

    with user_engine(user_id).connect() as conn:
        row = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='onboarding_state'"
            )
        ).first()
    assert row is not None
    _cleanup(path)


def test_ensure_user_db_migrates_stale_borrower_columns():
    """Existing per-user DBs must pick up columns added after first login."""
    user_id = "test-stale-migrate-user"
    path = user_db_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    init_user_db(user_id)
    from sqlalchemy import text

    from app.db.user_db import user_engine

    eng = user_engine(user_id)
    with eng.connect() as conn:
        conn.execute(text("ALTER TABLE borrower_profile DROP COLUMN monthly_rent"))
        conn.execute(text("ALTER TABLE borrower_profile DROP COLUMN rent_due_date"))
        conn.commit()
    ensure_user_db(user_id)
    with eng.connect() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(borrower_profile)")).fetchall()}
    assert "monthly_rent" in cols
    assert "rent_due_date" in cols
    _cleanup(path)


def test_init_user_db_creates_schema():
    user_id = "test-fresh-user"
    path = user_db_path(user_id)
    if path.exists():
        path.unlink()
    init_user_db(user_id)
    ensure_user_db(user_id)
    assert path.exists()
    _cleanup(path)


def test_existing_v1_database_upgrade_adds_d002_schema_idempotently():
    """The real per-user upgrade path preserves an existing V1 action row."""
    user_id = "test-d002-existing-v1"
    _cleanup(user_db_path(user_id))
    init_user_db(user_id)
    from sqlalchemy import text
    from sqlmodel import Session

    from app.core.cashflow_engine import calculate_cashflow
    from app.db.models import BorrowerProfile, FinancialAction
    from app.db.user_db import user_engine

    path = user_db_path(user_id)
    eng = user_engine(user_id)
    with Session(eng) as session:
        session.add(FinancialAction(id="v1-action", title="Keep me", action_type="call_provider"))
        session.add(BorrowerProfile(checking_balance=5000, target_buffer=100))
        session.commit()
    with eng.connect() as conn:
        # SQLite cannot drop a foreign-key column in place.  Reconstruct the
        # representative V1 action table only inside this isolated test DB.
        conn.execute(text("ALTER TABLE financial_actions RENAME TO financial_actions_d002"))
        conn.execute(
            text(
                "CREATE TABLE financial_actions ("
                "id TEXT PRIMARY KEY, title TEXT NOT NULL, action_type TEXT NOT NULL, "
                "status TEXT NOT NULL, priority INTEGER NOT NULL, "
                "expected_monthly_savings FLOAT NOT NULL, "
                "expected_total_savings FLOAT NOT NULL, "
                "linked_entity_type TEXT NOT NULL, linked_entity_id TEXT NOT NULL, "
                "notes TEXT NOT NULL, dismiss_reason TEXT NOT NULL, "
                "is_urgent BOOLEAN NOT NULL, blocked_by TEXT NOT NULL, "
                "created_at DATETIME NOT NULL)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO financial_actions ("
                "id, title, action_type, status, priority, expected_monthly_savings, "
                "expected_total_savings, linked_entity_type, linked_entity_id, notes, "
                "dismiss_reason, is_urgent, blocked_by, created_at) "
                "SELECT id, title, action_type, status, priority, "
                "expected_monthly_savings, expected_total_savings, "
                "linked_entity_type, linked_entity_id, notes, dismiss_reason, "
                "is_urgent, blocked_by, created_at FROM financial_actions_d002"
            )
        )
        conn.execute(text("DROP TABLE financial_actions_d002"))
        conn.execute(text("DROP TABLE opportunity_transitions"))
        conn.execute(text("DROP TABLE opportunity_candidates"))
        conn.execute(text("DROP TABLE opportunity_evidence"))
        conn.execute(text("DROP TABLE opportunity_research_runs"))
        conn.execute(text("DROP TABLE capability_assets"))
        conn.execute(text("DROP TABLE capability_skills"))
        conn.execute(text("DROP TABLE capability_profiles"))
        conn.commit()
    ensure_user_db(user_id)
    ensure_user_db(user_id)
    with eng.connect() as conn:
        tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        action_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(financial_actions)"))}
        action = conn.execute(
            text("SELECT title, opportunity_candidate_id FROM financial_actions WHERE id='v1-action'")
        ).one()
        indexes = {row[1] for row in conn.execute(text("PRAGMA index_list(financial_actions)"))}
    assert {
        "capability_profiles",
        "capability_skills",
        "capability_assets",
        "opportunity_candidates",
        "opportunity_transitions",
        "opportunity_research_runs",
        "opportunity_evidence",
    } <= tables
    assert "opportunity_candidate_id" in action_cols
    assert action == ("Keep me", None)
    assert "ix_financial_actions_opportunity_candidate_id" in indexes
    with Session(eng) as session:
        assert "money_state" in calculate_cashflow(session)
    _cleanup(path)


def test_get_user_session_ensures_once_per_process(monkeypatch):
    """The per-request hot path must not re-run migrations every call."""
    import app.db.user_db as user_db

    calls: list[str] = []

    def fake_ensure(uid: str) -> None:
        calls.append(uid)

    monkeypatch.setattr(user_db, "ensure_user_db", fake_ensure)

    class _DummySession:
        def __init__(self, _engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(user_db, "Session", _DummySession)

    user_db.clear_engine_caches()  # start clean
    uid = "ensure-once-user"
    for _ in range(3):
        next(user_db.get_user_session(uid), None)
    assert calls == [uid], "ensure must run exactly once per process for repeated requests"

    user_db.clear_engine_caches()
    next(user_db.get_user_session(uid), None)
    assert calls == [uid, uid], "clearing engine caches must allow a re-ensure (reset path)"

    user_db.clear_engine_caches()
