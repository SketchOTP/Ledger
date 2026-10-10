"""Test fixtures."""

import os

import pytest
from sqlmodel import Session, SQLModel, create_engine

os.environ["AI_PROVIDER"] = "mock"
os.environ["LEDGERMIND_AUTH_DISABLED"] = "1"

from app.db import models  # noqa: F401
from app.db.models import Bill, Debt, IncomeSource
from app.db.user_db import user_db_path


@pytest.fixture(autouse=True)
def _clear_dev_user_ai_settings():
    """Avoid per-user ai_settings.json leaking between tests in auth-disabled mode."""
    path = user_db_path("dev-local").parent / "ai_settings.json"
    path.unlink(missing_ok=True)
    yield
    path.unlink(missing_ok=True)


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def sample_data(session: Session):
    session.add(IncomeSource(source_name="Job", amount=2000, pay_frequency="biweekly"))
    session.add(Bill(name="Rent", amount=1200, required=True))
    session.add(Debt(creditor="Card", balance=3000, apr=22, minimum_payment=90))
    session.commit()
    return session
