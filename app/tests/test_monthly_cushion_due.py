"""Monthly cushion obligations stay visible, capped, and counted only once."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_db
from app.core.bill_schedule import _advance_months
from app.core.v2_today import build_today
from app.db.models import BorrowerProfile, CashCushionContribution, IncomeSource
from app.main import app


@pytest.fixture
def cushion_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        profile = BorrowerProfile(checking_balance=50000, cash_cushion_balance=200,
                                  target_buffer=1000, cushion_monthly_contribution=100,
                                  pay_period_allowance_enabled=True)
        session.add(profile)
        session.add(IncomeSource(source_name="Job", amount=2000, pay_frequency="biweekly",
                                 next_pay_date=date.today() + timedelta(days=4)))
        session.commit()
        app.dependency_overrides[get_db] = lambda: session
        try:
            yield TestClient(app), session, profile
        finally:
            app.dependency_overrides.clear()
    engine.dispose()


def test_future_monthly_deposit_is_visible_before_its_funding_date(cushion_client):
    client, session, profile = cushion_client
    profile.checking_balance = 250
    session.add(profile)
    session.commit()
    payload = client.get('/api/v2/today').json()
    allowance = payload['financial_state']['allowance']
    first = allowance['contributions'][0]
    assert first['date'] > date.today().isoformat()
    assert payload['financial_state']['today_breakdown']['cash_cushion_refill'] == 0
    assert payload['cushion_step'] == dict(amount=100, date=first['date'], recorded=False, can_record=False)
    assert client.post('/api/profile/cushion-contribution', json={'amount': 100}).status_code == 400
    assert profile.cash_cushion_balance == 200


@pytest.mark.parametrize('current,amount', [(0,100), (900,100), (950,50), (999.99,.01), (1000,0), (1200,0)])
def test_due_amount_adapts_to_current_balance_and_stops_at_goal(cushion_client, current, amount):
    client, session, profile = cushion_client
    profile.cash_cushion_balance = current
    session.add(profile)
    session.commit()
    step = client.get('/api/v2/today').json()['cushion_step']
    assert step['amount'] == amount
    assert step['recorded'] is (amount == 0)
    assert step['can_record'] is (amount > 0)
    assert step['date'] == date.today().isoformat()
    assert profile.cash_cushion_balance == current  # A projection never records a deposit.


def test_checking_off_deposit_updates_fund_without_double_counting_or_repeating_month(cushion_client):
    client, session, profile = cushion_client
    before = client.get('/api/v2/today').json()
    recorded = client.post('/api/profile/cushion-contribution', json={'amount': 100})
    assert recorded.status_code == 200, recorded.text
    after = client.get('/api/v2/today').json()
    assert after['financial_state']['allowance']['emergency_current'] == 300
    assert after['financial_state']['allowance']['remaining'] == before['financial_state']['allowance']['remaining']
    assert after['cashflow']['checking_balance'] == before['cashflow']['checking_balance'] == 50000
    assert after['cushion_step']['date'] >= _advance_months(date.today().replace(day=1), 1).isoformat()
    assert after['cushion_step']['amount'] == 100
    assert after['cushion_step']['can_record'] is False
    assert client.post('/api/profile/cushion-contribution', json={'amount': 100}).status_code == 400
    assert len(session.exec(select(CashCushionContribution)).all()) == 1
    assert profile.cash_cushion_balance == 300


def test_partial_month_contributions_reduce_remainder_and_final_deposit(cushion_client):
    client, session, profile = cushion_client
    assert client.post('/api/profile/cushion-contribution', json={'amount': 60}).status_code == 200
    assert client.get('/api/v2/today').json()['cushion_step']['amount'] == 40
    assert client.post('/api/profile/cushion-contribution', json={'amount': 40}).status_code == 200
    assert client.get('/api/v2/today').json()['cushion_step']['can_record'] is False
    profile.cash_cushion_balance = 975
    session.add(profile)
    session.commit()
    upcoming = client.get('/api/v2/today').json()['cushion_step']
    assert upcoming['amount'] == 25
    assert upcoming['date'] > date.today().isoformat()


def test_final_deposit_reaches_goal_then_disappears_and_cannot_overfund(cushion_client):
    client, session, profile = cushion_client
    profile.cash_cushion_balance = 950
    session.add(profile)
    session.commit()
    assert client.get('/api/v2/today').json()['cushion_step']['amount'] == 50
    assert client.post('/api/profile/cushion-contribution', json={'amount': 50.01}).status_code == 400
    assert client.post('/api/profile/cushion-contribution', json={'amount': 50}).status_code == 200
    payload = client.get('/api/v2/today').json()
    assert profile.cash_cushion_balance == 1000
    assert payload['cushion_step']['amount'] == 0
    assert payload['cushion_step']['recorded'] is True
    assert payload['financial_state']['allowance']['contributions'] == []
    assert client.post('/api/profile/cushion-contribution', json={'amount': .01}).status_code == 400


def test_unfunded_obligation_stays_visible_without_an_enabled_check(cushion_client):
    client, session, profile = cushion_client
    profile.checking_balance = 0
    session.add(profile)
    session.commit()
    payload = build_today(session)
    assert payload['cushion_step']['amount'] == 100
    assert payload['cushion_step']['recorded'] is False
    assert payload['cushion_step']['can_record'] is False
    assert client.post('/api/profile/cushion-contribution', json={'amount': 100}).status_code == 400
    assert profile.cash_cushion_balance == 200
