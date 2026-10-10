"""D009R1 state-root and code/static separation contracts."""

from app.config import ROOT_DIR, Settings, resolve_state_path
from app.db import user_db


def test_explicit_data_dir_replaces_checkout_data_root(tmp_path):
    state = Settings(
        LEDGERMIND_DATA_DIR=str(tmp_path / "household-state"),
        DATABASE_URL="sqlite:////old-checkout/data/ledgermind.db",
        EVENT_LOG_DIR=str(tmp_path / "old-events"),
    )

    assert state.data_dir == (tmp_path / "household-state").resolve()
    assert state.database_url == f"sqlite:///{state.data_dir / 'ledgermind.db'}"
    assert state.event_log_dir == state.data_dir / "events"
    assert state.reports_dir == state.data_dir / "reports"
    assert state.evidence_dir == state.data_dir / "evidence"


def test_unset_data_dir_keeps_legacy_default(monkeypatch):
    monkeypatch.delenv("LEDGERMIND_DATA_DIR", raising=False)
    state = Settings(_env_file=None)

    assert state.data_dir == ROOT_DIR / "data"
    assert state.database_url == f"sqlite:///{ROOT_DIR / 'data' / 'ledgermind.db'}"


def test_user_database_constants_follow_explicit_state_root(monkeypatch, tmp_path):
    state_root = tmp_path / "state"
    monkeypatch.setattr(user_db, "USERS_DIR", state_root / "users")
    monkeypatch.setattr(user_db, "MASTER_DB_PATH", state_root / "master.db")

    assert user_db.user_db_path("synthetic-user") == state_root / "users" / "synthetic-user" / "ledgermind.db"
    assert user_db.MASTER_DB_PATH.parent == state_root


def test_legacy_relative_data_paths_resolve_under_state_root(monkeypatch, tmp_path):
    state_root = tmp_path / "shared-state"
    monkeypatch.setattr("app.config.settings.data_dir", state_root)
    legacy_file = state_root / "users" / "synthetic" / "file.txt"
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_text("fixture", encoding="utf-8")

    assert resolve_state_path("data/users/synthetic/file.txt") == legacy_file
    assert resolve_state_path("evidence/fixture.txt") == state_root / "evidence" / "fixture.txt"


def test_shipped_static_assets_stay_in_checkout():
    from app.research.providers.curated_rates import curated_path

    assert (ROOT_DIR / "data" / "import_templates").is_dir()
    # Approved curated rates are operator-owned and may be absent from a clean
    # checkout; providers already report that absence instead of inventing data.
    assert curated_path() == ROOT_DIR / "data" / "research" / "curated_rates.json"
    assert not str(ROOT_DIR / "data" / "import_templates").startswith(str(ROOT_DIR / "data" / "users"))
