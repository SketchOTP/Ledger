"""Regression test: SQLite connections must tolerate concurrency.

Reproduces the "database is locked" -> HTTP 500 cascade by hammering a
file-backed SQLite DB from several threads. Importing app.db.session
registers the global connect-time PRAGMA listener (WAL + busy_timeout)
that makes this survivable.
"""

import threading

from sqlalchemy import text
from sqlmodel import create_engine

import app.db.session  # noqa: F401  (registers the global PRAGMA listener)


def _engine(tmp_path):
    url = f"sqlite:///{tmp_path / 'concurrency.db'}"
    return create_engine(url, connect_args={"check_same_thread": False})


def test_pragmas_applied(tmp_path):
    eng = _engine(tmp_path)
    with eng.connect() as conn:
        journal = conn.execute(text("PRAGMA journal_mode")).scalar()
        busy = conn.execute(text("PRAGMA busy_timeout")).scalar()
    assert journal == "wal"
    assert busy >= 30000


def test_concurrent_writes_do_not_lock(tmp_path):
    eng = _engine(tmp_path)
    with eng.connect() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER)"))
        conn.commit()

    errors: list[Exception] = []

    def writer(start: int) -> None:
        try:
            with eng.connect() as conn:
                for i in range(start, start + 50):
                    conn.execute(text("INSERT INTO t (v) VALUES (:v)"), {"v": i})
                    conn.commit()
        except Exception as exc:  # noqa: BLE001 - surface any locking failure
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n * 1000,)) for n in range(8)]
    for thr in threads:
        thr.start()
    for thr in threads:
        thr.join()

    assert not errors, f"concurrent writes raised: {errors!r}"
    with eng.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM t")).scalar()
    assert count == 8 * 50
