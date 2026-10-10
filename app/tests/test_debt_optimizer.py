"""Debt optimizer tests."""

from app.core.debt_optimizer import rank_debts
from app.db.models import Debt


def test_avalanche_ranking(session):
    session.add(Debt(creditor="Low", balance=1000, apr=10, minimum_payment=25))
    session.add(Debt(creditor="High", balance=2000, apr=24, minimum_payment=50))
    session.commit()
    result = rank_debts(session, "avalanche")
    assert result["order"][0]["creditor"] == "High"


def test_snowball_ranking(session):
    session.add(Debt(creditor="Small", balance=500, apr=15, minimum_payment=25))
    session.add(Debt(creditor="Large", balance=5000, apr=12, minimum_payment=100))
    session.commit()
    result = rank_debts(session, "snowball")
    assert result["order"][0]["creditor"] == "Small"
