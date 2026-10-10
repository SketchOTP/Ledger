from datetime import date, timedelta

import pytest
from sqlmodel import select

from app.core.bill_schedule import _advance_months
from app.core.pay_period_allowance import build_pay_period_allowance
from app.db.models import BorrowerProfile, CashCushionContribution, IncomeSource, Transaction
from app.db.session import _sqlite_migrate_cash_cushion

DAY = date(2026, 10, 7)


def setup_plan(session, cash=3000, current=200, goal=1000, monthly=100):
    profile = BorrowerProfile(checking_balance=cash, cash_cushion_balance=current,
                              target_buffer=goal, cushion_monthly_contribution=monthly,
                              pay_period_allowance_enabled=True)
    session.add(profile)
    session.add(IncomeSource(source_name='Main', amount=1000, next_pay_date=DAY,
                             pay_frequency='biweekly'))
    session.commit()
    return profile


def plan(session, rows=None, cash=3000, day=DAY):
    return build_pay_period_allowance(session, as_of=day, through=_advance_months(day, 6),
                                     opening_cash=cash, payment_rows=rows or [])


def test_allowance_is_not_entire_surplus_and_preserves_future_allowances_and_runway(session):
    setup_plan(session, monthly=0)
    result = plan(session)
    assert 0 < result['remaining'] < 2800 / 10
    assert result['period_end'] == '2026-10-20'
    assert result['comfort_holdback'] == result['safe_period_capacity'] > result['budget']
    assert all(row['balanceAfterSpending'] + .005 >= row['cushion'] + result['comfort_holdback']
               for row in result['projection'])
    assert (result['projection'][-1]['balanceAfterSpending']
            - result['comfort_holdback'] - result['planned_retained']) < 201


def test_monthly_savings_accumulates_without_claiming_actual_money_saved(session):
    profile = setup_plan(session)
    result = plan(session)
    assert [row['amount'] for row in result['contributions']] == [100] * 7
    assert result['projected_emergency'] == 900
    assert profile.cash_cushion_balance == 200
    assert session.exec(select(CashCushionContribution)).all() == []
    assert all(row['balanceAfterSpending'] + .005 >= row['cushion'] + result['budget']
               for row in result['projection'])


def test_contributions_stop_at_goal_and_final_step_is_partial(session):
    setup_plan(session, current=950)
    result = plan(session)
    assert [row['amount'] for row in result['contributions']] == [50]
    assert result['projected_emergency'] == 1000


def test_contribution_in_previous_calendar_month_does_not_block_this_month(session):
    setup_plan(session)
    session.add(CashCushionContribution(amount=100, contribution_date=date(2026, 9, 30)))
    session.commit()
    assert plan(session)['contributions'][0]['amount'] == 100
    session.add(CashCushionContribution(amount=60, contribution_date=DAY))
    session.commit()
    assert plan(session)['contributions'][0]['amount'] == 40


def test_savings_wait_for_income_that_does_not_squeeze_essentials(session):
    setup_plan(session, cash=250, current=200)
    rows = [{'date': '2026-10-09', 'kind': 'income', 'amount': 100},
            {'date': '2026-10-10', 'kind': 'obligation', 'amount': 100},
            {'date': '2026-10-21', 'kind': 'income', 'amount': 2000}]
    result = plan(session, rows, cash=250)
    assert result['contributions'][0]['date'] == '2026-10-21'
    assert result['remaining'] <= 25
    assert result['funding_shortfall'] == 0


@pytest.mark.parametrize('cash,current', [(0, 0), (-50, 0), (500, None), (100, 100)])
def test_shortfalls_or_unknown_reserve_never_release_allowance(session, cash, current):
    setup_plan(session, cash=cash, current=current)
    assert plan(session, cash=cash)['remaining'] == 0


def test_month_five_bill_is_already_reserved_today(session):
    setup_plan(session, monthly=0)
    baseline = plan(session)['budget']
    result = plan(session, [{'date': '2027-03-06', 'kind': 'obligation', 'amount': 1500}])
    assert result['budget'] < baseline
    assert min(row['balanceAfterSpending'] - row['cushion'] for row in result['projection']) >= result['budget'] - .005


def test_spent_allowance_is_not_replenished_by_reconciliation_or_refresh(session):
    setup_plan(session, monthly=0)
    before = plan(session)
    purchase = Transaction(amount=20, category='Discretionary', transaction_date=DAY,
                           reflected_in_checking=False)
    session.add(purchase)
    session.commit()
    pending = plan(session)
    assert pending['remaining'] == pytest.approx(before['remaining'] - 20)
    purchase.reflected_in_checking = True
    session.add(purchase)
    session.commit()
    confirmed = plan(session, cash=2980)
    assert confirmed['remaining'] == pending['remaining']
    assert plan(session, cash=2980)['remaining'] == confirmed['remaining']
    next_period = plan(session, cash=2980, day=DAY + timedelta(days=14))
    assert next_period['spent'] == 0
    assert next_period['period_start'] == '2026-10-21'


def test_existing_fund_in_savings_is_not_reserved_from_checking_twice(session):
    profile = setup_plan(session, current=1000, monthly=0)
    profile.emergency_fund_balance = 1000
    session.add(profile)
    session.commit()
    result = plan(session)
    assert result['safe_period_capacity'] == 3000 / 15
    assert result['remaining'] == 180
    assert result['retained_per_period'] == 20


def test_policy_migration_is_additive_and_idempotent():
    from sqlalchemy import create_engine, text
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE borrower_profile (id TEXT PRIMARY KEY, checking_balance REAL)'))
        connection.execute(text("INSERT INTO borrower_profile VALUES ('owner', 123)"))
    _sqlite_migrate_cash_cushion(engine)
    _sqlite_migrate_cash_cushion(engine)
    with engine.connect() as connection:
        row = connection.execute(text(
            'SELECT checking_balance, cushion_monthly_contribution, pay_period_allowance_enabled '
            'FROM borrower_profile')).one()
        assert tuple(row) == (123, 100, 0)


def test_old_checking_does_not_release_past_daily_or_saturday_spending(session):
    from datetime import datetime

    from app.core.budget_engine import planned_spending_daily_events
    from app.db.models import BudgetCategory
    profile = setup_plan(session)
    profile.checking_confirmed_at = datetime(2026, 10, 9, 10)
    session.add(profile)
    session.add(BudgetCategory(name='Daily needs', period='day', usual_amount=30, monthly_limit=900))
    session.add(BudgetCategory(name='Groceries', period='week', usual_amount=150,
                               monthly_limit=650, planned_weekday=5))
    session.commit()
    rows = planned_spending_daily_events(session, date(2026, 10, 12), date(2026, 10, 12))
    assumed = [row for row in rows if row['name'].startswith('Assumed')]
    assert sum(row['amount'] for row in assumed) == 240  # Fri, Sat, Sun + Saturday groceries.
    profile.checking_confirmed_at = datetime(2026, 10, 12, 10)
    session.add(profile)
    session.commit()
    assert not any(row['name'].startswith('Assumed') for row in
                   planned_spending_daily_events(session, date(2026, 10, 12), date(2026, 10, 12)))


def test_allowance_purchase_stays_consumed_after_bank_confirmation(tmp_path):
    from fastapi.testclient import TestClient
    from sqlmodel import Session, SQLModel, create_engine

    from app.api.deps import get_db
    from app.main import app

    engine = create_engine(f"sqlite:///{tmp_path / 'allowance.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    setup_plan(session, monthly=0)
    app.dependency_overrides[get_db] = lambda: session
    try:
        client = TestClient(app)
        response = client.post('/api/spending/transactions', json={
            'amount': 20, 'category': 'Discretionary', 'transaction_date': date.today().isoformat()})
        assert response.status_code == 200, response.text
        assert client.get('/api/v2/today').json()['checking_activity_pending'] == 0
        assert client.get('/api/v2/today').json()['financial_state']['allowance']['spent'] == 20
        assert client.get('/api/spending/pending-checking').json()['count'] == 1
        response = client.post('/api/profile/borrower', json={
            'checking_balance': 2980, 'checking_balance_confirmed': True, 'includes_recorded_spending': True})
        assert response.status_code == 200, response.text
        assert session.exec(select(Transaction)).one().reflected_in_checking is True
        assert client.get('/api/v2/today').json()['checking_activity_pending'] == 0
    finally:
        app.dependency_overrides.clear()
        session.close()


@pytest.mark.parametrize('amount', [-1, 'NaN', 'Infinity'])
def test_monthly_emergency_amount_must_be_finite_nonnegative(session, amount):
    from fastapi.testclient import TestClient

    from app.api.deps import get_db
    from app.main import app

    setup_plan(session)
    app.dependency_overrides[get_db] = lambda: session
    try:
        result = TestClient(app).post('/api/profile/borrower', json={'cushion_monthly_contribution': amount})
        assert result.status_code == 422, result.text
        assert session.exec(select(BorrowerProfile)).one().cushion_monthly_contribution == 100
    finally:
        app.dependency_overrides.clear()


def test_overspending_reduces_future_budget_without_creating_negative_spending_credit(session):
    setup_plan(session, monthly=0)
    before = plan(session)
    session.add(Transaction(amount=500, category='Discretionary', transaction_date=DAY,
                            reflected_in_checking=True))
    session.commit()
    after = plan(session, cash=2500)
    assert after['remaining'] == 0
    assert after['budget'] < before['budget']
    assert all(row['balanceAfterSpending'] + .005 >= row['cushion'] + after['budget']
               for row in after['projection'])


def test_unconfirmed_bill_cannot_release_allowance_through_other_api_callers(session):
    from datetime import datetime, timedelta

    from app.db.models import PaymentArchiveEvent

    profile = setup_plan(session, monthly=0)
    profile.checking_confirmed_at = datetime(2026, 10, 7, 9)
    session.add(profile)
    session.add(PaymentArchiveEvent(action='marked_paid', kind='bill', bill_id='example',
                                   scheduled_date=DAY, marked_date=DAY, amount=100, name='Example bill',
                                   created_at=datetime(2026, 10, 7, 10)))
    session.commit()
    assert plan(session)['remaining'] == 0
    profile.checking_confirmed_at += timedelta(hours=2)
    session.add(profile)
    session.commit()
    assert plan(session)['remaining'] > 0


def test_monthly_saving_can_be_earmarked_after_paycheck_is_already_received(session):
    setup_plan(session)
    result = plan(session, [{'date': '2026-10-21', 'kind': 'income', 'amount': 1000}])
    assert result['contributions'][0]['date'] == DAY.isoformat()


def test_allowance_leaves_ten_percent_of_safe_allocation_inside_discretionary_plan(session):
    setup_plan(session, monthly=0)
    result = plan(session)
    capacity = round(result['safe_period_capacity'] * 100)
    assert round(result['budget'] * 100) == capacity * 90 // 100
    assert result['retention_percent'] == 10
    assert result['budget'] + result['retained_per_period'] == pytest.approx(result['safe_period_capacity'])
    future_days = (date.fromisoformat(result['planning_through']) - date.fromisoformat(result['next_reset'])).days
    periods = 2 + future_days // 14
    assert result['total_discretionary_planned'] == pytest.approx(periods * result['safe_period_capacity'])
    assert result['total_discretionary_planned'] == pytest.approx(
        periods * result['budget'] + result['planned_retained'])
    assert result['projection'][-1]['balanceAfterSpending'] >= (
        result['emergency_current'] + result['comfort_holdback'] + result['planned_retained'] - .005)
    assert session.exec(select(Transaction)).all() == []
    assert session.exec(select(CashCushionContribution)).all() == []


def test_full_released_allowance_still_leaves_cash_to_carry_forward_over_successive_periods(session):
    setup_plan(session, cash=200, current=0, goal=0, monthly=0)
    cash = 200
    previous_cash = cash
    allowances = []
    for index in range(4):
        day = DAY + timedelta(days=14 * index)
        through = _advance_months(day, 6)
        rows = [{'date': (day + timedelta(days=14 * i)).isoformat(), 'kind': 'income', 'amount': 100}
                for i in range(1, 15) if day + timedelta(days=14 * i) <= through]
        result = plan(session, rows, cash=cash, day=day)
        assert 0 < result['budget'] < 100
        allowances.append(result['budget'])
        session.add(Transaction(amount=result['budget'], category='Discretionary', transaction_date=day,
                                reflected_in_checking=True))
        session.commit()
        after = plan(session, rows, cash=round(cash - result['budget'], 2), day=day)
        assert after['remaining'] == 0
        assert after['retained_per_period'] > 0
        cash = round(cash - result['budget'] + 100, 2)
        assert cash > previous_cash
        previous_cash = cash
    assert allowances[0] == 90
    assert allowances == sorted(allowances)
    assert cash > 230


def test_discretionary_retention_is_separate_from_the_only_emergency_cushion(session):
    setup_plan(session, current=42.75, cash=5000)
    result = plan(session, cash=5000)
    assert result['emergency_current'] == 42.75
    assert result['projected_emergency'] == pytest.approx(42.75 + sum(row['amount'] for row in result['contributions']))
    assert result['retained_per_period'] > 0
    for row in result['projection']:
        expected = 42.75 + sum(step['amount'] for step in result['contributions'] if step['date'] <= row['date'])
        assert row['cushion'] == pytest.approx(expected)
        assert row['surprises'] == 0


def test_daily_and_grocery_transactions_do_not_consume_discretionary_allowance(session):
    setup_plan(session, monthly=0)
    session.add(Transaction(amount=30, category='Daily Needs', transaction_date=DAY, reflected_in_checking=True))
    session.add(Transaction(amount=150, category='Groceries', transaction_date=DAY, reflected_in_checking=True))
    session.commit()
    result = plan(session, cash=2820)
    assert result['spent'] == 0
    assert result['remaining'] == result['budget']


def test_overspending_reduces_retained_plan_instead_of_inventing_saved_cash(session):
    setup_plan(session, monthly=0)
    before = plan(session)
    amount = round(before['budget'] + before['retained_per_period'] / 2, 2)
    session.add(Transaction(amount=amount, category='Discretionary', transaction_date=DAY, reflected_in_checking=True))
    session.commit()
    after = plan(session, cash=round(3000 - amount, 2))
    assert after['remaining'] == 0
    assert after['planned_retained'] < before['planned_retained']
    assert after['emergency_current'] == before['emergency_current']
    assert after['total_discretionary_planned'] == pytest.approx(before['total_discretionary_planned'] - amount)
