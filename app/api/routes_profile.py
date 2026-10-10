"""CFO profile, mortgage, credit cards, and offers."""

import math
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlmodel import Session, select

from app.api.deps import get_db
from app.core.account_balances import account_totals
from app.core.cashflow_engine import calculate_cashflow
from app.core.credit_card_optimizer import analyze_credit_card
from app.core.dti_engine import calculate_dti
from app.core.entry_validation import (
    coerce_date_fields,
    normalize_mortgage_payload,
    raise_http_if_invalid,
    validate_cash_balance,
    validate_credit_card,
    validate_mortgage,
    validate_rent,
)
from app.core.health_score import calculate_health_score
from app.core.mortgage_optimizer import analyze_mortgage
from app.core.mortgage_payment import mark_mortgage_paid
from app.core.required_payment_deferral import current_payment_deferral, record_approved_payment_deferral
from app.core.v2_opportunities import refresh_household_decision_dependents
from app.db.models import (
    BorrowerProfile,
    CashCushionContribution,
    CreditCard,
    LiquidAccount,
    MortgageAccount,
    PaymentArchiveEvent,
    Transaction,
)
from app.events.bus import event_bus
from app.events.types import Event, EventType

router = APIRouter(prefix="/api/profile", tags=["profile"])


class CushionContributionBody(BaseModel):
    amount: float


def _ensure_mortgage_housing_status(session: Session) -> None:
    """Mark profile as mortgage when a loan account is saved."""
    profile = session.exec(select(BorrowerProfile)).first()
    if not profile:
        profile = BorrowerProfile(housing_status="mortgage")
        session.add(profile)
    elif (profile.housing_status or "").lower() != "mortgage":
        profile.housing_status = "mortgage"
        profile.updated_at = datetime.now(UTC).replace(tzinfo=None)
        session.add(profile)


@router.get("/health")
def health_score(session: Session = Depends(get_db)):
    return calculate_health_score(session)


@router.get("/dti")
def dti(session: Session = Depends(get_db)):
    result = calculate_dti(session)
    event_bus.emit(Event(event_type=EventType.DTI_RECALCULATED.value, payload=result))
    return result


@router.get("/borrower")
def get_borrower(session: Session = Depends(get_db)):
    return session.exec(select(BorrowerProfile)).first()


@router.post("/borrower")
def upsert_borrower(data: dict, session: Session = Depends(get_db)):
    data.pop("checking_confirmed_at", None)
    checking_confirmed = data.pop("checking_balance_confirmed", False)
    if not isinstance(checking_confirmed, bool):
        raise HTTPException(400, "Confirm whether this is your current bank balance.")
    includes_recorded_spending = data.pop("includes_recorded_spending", False)
    if not isinstance(includes_recorded_spending, bool):
        raise HTTPException(400, "Confirm whether recorded purchases are in this balance.")
    if (includes_recorded_spending or checking_confirmed) and "checking_balance" not in data:
        raise HTTPException(400, "Enter the current checking balance first.")
    checking_updated = "checking_balance" in data
    status = (data.get("housing_status") or "").lower()
    if status == "rent":
        vr = validate_rent(data)
        raise_http_if_invalid(vr)
    if "checking_balance" in data or "emergency_fund_balance" in data:
        vr = validate_cash_balance(data)
        raise_http_if_invalid(vr)
    elif any(key in data for key in ("cash_cushion_balance", "target_buffer", "cushion_monthly_contribution")):
        vr = validate_cash_balance({**data, "checking_balance": 0})
        raise_http_if_invalid(vr)
    if "pay_period_allowance_enabled" in data and not isinstance(data["pay_period_allowance_enabled"], bool):
        raise HTTPException(400, "Choose whether pay-period allowance planning is enabled.")
    data = coerce_date_fields(data, "rent_due_date")
    obj = session.exec(select(BorrowerProfile)).first()
    if not obj:
        obj = BorrowerProfile()
    for k, v in data.items():
        if hasattr(obj, k):
            setattr(obj, k, v)
    if obj.cash_cushion_balance is not None:
        account_cash = sum(
            max(0.0, float(account.balance or 0))
            for account in session.exec(select(LiquidAccount)).all()
            if account.include_in_cashflow
        )
        available = (
            max(0.0, float(obj.checking_balance or 0))
            + max(0.0, float(obj.emergency_fund_balance or 0))
            + account_cash
        )
        if float(obj.cash_cushion_balance) > available + 0.005:
            raise HTTPException(400, "Current cushion cannot exceed your recorded available money")
    obj.updated_at = datetime.now(UTC).replace(tzinfo=None)
    if checking_updated and checking_confirmed:
        obj.checking_confirmed_at = obj.updated_at
    session.add(obj)
    if includes_recorded_spending:
        pending = session.exec(select(Transaction).where(
            or_(Transaction.planned_budget_category_id.is_not(None),
                func.lower(Transaction.category) == "discretionary"),
            Transaction.reflected_in_checking.is_(False),
            Transaction.transaction_date <= date.today(),
        )).all()
        for transaction in pending:
            transaction.reflected_in_checking = True
            session.add(transaction)
    session.commit()
    session.refresh(obj)
    refresh_household_decision_dependents(session, {"cash"})
    event_bus.emit(Event(event_type=EventType.BORROWER_PROFILE_UPDATED.value, payload={"id": obj.id}))
    if data.get("credit_score"):
        event_bus.emit(Event(event_type=EventType.CREDIT_SCORE_UPDATED.value, payload={"score": data["credit_score"]}))
    return obj


@router.post("/cushion-contribution")
def record_cushion_contribution(body: CushionContributionBody, session: Session = Depends(get_db)):
    """Confirm money set aside within accessible cash; never move funds."""
    today = date.today()
    if not math.isfinite(body.amount) or body.amount <= 0:
        raise HTTPException(400, "Enter an amount to set aside.")
    profile = session.exec(select(BorrowerProfile)).first()
    recent = session.exec(select(CashCushionContribution).where(
        CashCushionContribution.contribution_date >= (
            today.replace(day=1) if profile and profile.pay_period_allowance_enabled else today - timedelta(days=29)
        ),
    )).first()
    if recent and not (profile and profile.pay_period_allowance_enabled):
        raise HTTPException(409, "This cycle's cushion step is already recorded.")
    profile = session.exec(select(BorrowerProfile)).first()
    if not profile or profile.cash_cushion_balance is None:
        raise HTTPException(400, "Enter your current cushion amount in Money first.")
    if profile.checking_confirmed_at and session.exec(select(PaymentArchiveEvent).where(
        PaymentArchiveEvent.created_at > profile.checking_confirmed_at,
        PaymentArchiveEvent.action.in_(["marked_paid", "marked_received"]),
    )).first():
        raise HTTPException(409, "Update checking after your recent payment or income first.")
    snapshot = calculate_cashflow(session).get("money_state") or {}
    # An explicit earmark stays inside the already-protected cushion; it is
    # not another expense deducted from discretionary money.
    funded_cushion = min(
        float(profile.target_buffer or 0),
        float(snapshot.get("dated_lowest_after_normal_spending") or 0),
        float(account_totals(session)["checking"]),
    )
    available_to_earmark = max(0.0, funded_cushion - float(profile.cash_cushion_balance))
    allowance = snapshot.get("allowance")
    if allowance:
        available_to_earmark = next((row["amount"] for row in allowance["contributions"]
                                     if row["date"] <= today.isoformat()), 0)
        if not allowance["known"] or allowance["funding_shortfall"] > 0:
            available_to_earmark = 0
    if body.amount > available_to_earmark + 0.005:
        raise HTTPException(400, "This amount would use money needed for bills or daily essentials.")
    profile.cash_cushion_balance = round(float(profile.cash_cushion_balance) + body.amount, 2)
    profile.updated_at = datetime.now(UTC).replace(tzinfo=None)
    contribution = CashCushionContribution(amount=round(body.amount, 2), contribution_date=today)
    session.add(profile)
    session.add(contribution)
    session.commit()
    refresh_household_decision_dependents(session, {"cash"})
    return {"ok": True, "amount": contribution.amount, "cash_cushion_balance": profile.cash_cushion_balance}


@router.get("/accounts")
def list_accounts(session: Session = Depends(get_db)):
    return session.exec(select(LiquidAccount).order_by(LiquidAccount.created_at.asc())).all()


@router.post("/accounts")
def create_account(data: dict, session: Session = Depends(get_db)):
    apy = data.get("current_apy")
    if apy is not None and (float(apy) < 0 or float(apy) > 100):
        raise HTTPException(400, "current_apy must be between 0 and 100")
    obj = LiquidAccount(
        account_name=str(data.get("account_name") or "").strip() or "Account",
        account_type=str(data.get("account_type") or "checking"),
        institution=str(data.get("institution") or ""),
        balance=float(data.get("balance") or 0),
        include_in_cashflow=bool(data.get("include_in_cashflow", True)),
        current_apy=float(apy) if apy is not None else None,
        apy_as_of=coerce_date_fields({"apy_as_of": data.get("apy_as_of")}, "apy_as_of").get("apy_as_of"),
    )
    obj.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(obj)
    session.commit()
    session.refresh(obj)
    refresh_household_decision_dependents(session, {"account"})
    session.refresh(obj)
    return obj


@router.put("/accounts/{account_id}")
def update_account(account_id: str, data: dict, session: Session = Depends(get_db)):
    obj = session.get(LiquidAccount, account_id)
    if not obj:
        raise HTTPException(404, "Account not found")
    for k in ("account_name", "account_type", "institution", "include_in_cashflow"):
        if k in data:
            setattr(obj, k, data[k])
    if "balance" in data:
        obj.balance = float(data["balance"] or 0)
    if "current_apy" in data:
        apy = data.get("current_apy")
        if apy is not None and (float(apy) < 0 or float(apy) > 100):
            raise HTTPException(400, "current_apy must be between 0 and 100")
        obj.current_apy = float(apy) if apy is not None else None
    if "apy_as_of" in data:
        obj.apy_as_of = coerce_date_fields({"apy_as_of": data.get("apy_as_of")}, "apy_as_of").get("apy_as_of")
    obj.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(obj)
    session.commit()
    session.refresh(obj)
    refresh_household_decision_dependents(session, {"account"})
    session.refresh(obj)
    return obj


@router.delete("/accounts/{account_id}")
def delete_account(account_id: str, session: Session = Depends(get_db)):
    obj = session.get(LiquidAccount, account_id)
    if not obj:
        raise HTTPException(404, "Account not found")
    session.delete(obj)
    session.commit()
    refresh_household_decision_dependents(session, {"account"})
    return {"ok": True}

@router.get("/mortgages")
def list_mortgages(session: Session = Depends(get_db)):
    mortgages = session.exec(select(MortgageAccount)).all()
    return [_mortgage_result(session, m) for m in mortgages]


def _deferral_result(session: Session, kind: str, entity_id: str, due: date | None) -> dict | None:
    approved = current_payment_deferral(session, kind, entity_id, due)
    return {
        "original_due_date": approved.original_due_date.isoformat(),
        "deferred_due_date": approved.deferred_due_date.isoformat(),
    } if approved else None


def _mortgage_result(session: Session, mortgage: MortgageAccount) -> dict:
    original_due = mortgage.due_by_date or mortgage.start_date
    approved = _deferral_result(session, "mortgage", mortgage.id, original_due)
    return {
        "account": mortgage,
        "analysis": analyze_mortgage(mortgage),
        "approved_deferral": approved,
        "next_due_date": approved["deferred_due_date"] if approved else (
            original_due.isoformat() if original_due else None
        ),
    }


@router.post("/mortgages")
def create_mortgage(data: dict, session: Session = Depends(get_db)):
    vr = validate_mortgage(data)
    raise_http_if_invalid(vr)
    _ensure_mortgage_housing_status(session)
    payload = normalize_mortgage_payload(data)
    obj = MortgageAccount(**{k: v for k, v in payload.items() if hasattr(MortgageAccount, k)})
    session.add(obj)
    session.commit()
    session.refresh(obj)
    refresh_household_decision_dependents(session, {"mortgage"})
    session.refresh(obj)
    event_bus.emit(Event(event_type=EventType.MORTGAGE_UPDATED.value, payload={"id": obj.id}))
    analysis = analyze_mortgage(obj, data.get("estimated_market_rate"))
    if analysis["recommendation"] == "compare_lenders":
        event_bus.emit(Event(event_type=EventType.MORTGAGE_REFINANCE_CANDIDATE.value, payload=analysis))
    return {"account": obj, "analysis": analysis}


@router.put("/mortgages/{mortgage_id}")
def update_mortgage(mortgage_id: str, data: dict, session: Session = Depends(get_db)):
    obj = session.get(MortgageAccount, mortgage_id)
    if not obj:
        raise HTTPException(404, "Mortgage not found")
    vr = validate_mortgage(data)
    raise_http_if_invalid(vr)
    _ensure_mortgage_housing_status(session)
    payload = normalize_mortgage_payload(data)
    if current_payment_deferral(session, "mortgage", mortgage_id, obj.due_by_date or obj.start_date):
        timing_fields = ("due_by_date", "recurrence", "monthly_payment", "escrow_amount")
        if any(key in payload and payload[key] != getattr(obj, key) for key in timing_fields):
            raise HTTPException(400, "Pay the approved one-time payment before changing its schedule")
    for k, v in payload.items():
        if hasattr(obj, k):
            setattr(obj, k, v)
    obj.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(obj)
    session.commit()
    session.refresh(obj)
    refresh_household_decision_dependents(session, {"mortgage"})
    session.refresh(obj)
    event_bus.emit(Event(event_type=EventType.MORTGAGE_UPDATED.value, payload={"id": obj.id}))
    from app.core.cashflow_engine import calculate_cashflow

    event_bus.emit(
        Event(
            event_type=EventType.CASHFLOW_RECALCULATED.value,
            payload=calculate_cashflow(session),
        )
    )
    return {"account": obj, "analysis": analyze_mortgage(obj, data.get("estimated_market_rate"))}


@router.post("/mortgages/{mortgage_id}/approved-deferral")
def approve_one_mortgage_date(mortgage_id: str, data: dict, session: Session = Depends(get_db)):
    mortgage = session.get(MortgageAccount, mortgage_id)
    if not mortgage:
        raise HTTPException(404, "Mortgage not found")
    try:
        record_approved_payment_deferral(
            session, "mortgage", mortgage_id,
            original_due_date=date.fromisoformat(str(data.get("original_due_date") or "")),
            deferred_due_date=date.fromisoformat(str(data.get("deferred_due_date") or "")),
            payee_approved=data.get("payee_approved") is True,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    cashflow = calculate_cashflow(session)
    refresh_household_decision_dependents(session, {"mortgage"}, cashflow=cashflow)
    return _mortgage_result(session, mortgage)


@router.post("/mortgages/{mortgage_id}/mark-paid")
def mark_mortgage_paid_route(
    mortgage_id: str,
    data: dict,
    session: Session = Depends(get_db),
):
    mortgage = session.get(MortgageAccount, mortgage_id)
    if not mortgage:
        raise HTTPException(404, "Mortgage not found")
    payload = coerce_date_fields(data, "due_date", "paid_date")
    due = payload.get("due_date")
    if not due:
        raise HTTPException(400, "due_date is required")
    inst, next_due = mark_mortgage_paid(
        session,
        mortgage,
        due,
        paid_date=payload.get("paid_date"),
        amount=payload.get("amount"),
    )
    cashflow = calculate_cashflow(session)
    refresh_household_decision_dependents(session, {"mortgage"}, cashflow=cashflow)
    event_bus.emit(
        Event(
            event_type=EventType.CASHFLOW_RECALCULATED.value,
            payload=cashflow,
        )
    )
    return {
        "ok": True,
        "instance": inst,
        "next_due_date": next_due.isoformat() if next_due else None,
    }


@router.delete("/mortgages/{mortgage_id}")
def delete_mortgage(mortgage_id: str, session: Session = Depends(get_db)):
    obj = session.get(MortgageAccount, mortgage_id)
    if not obj:
        raise HTTPException(404, "Mortgage not found")
    session.delete(obj)
    session.commit()
    cashflow = calculate_cashflow(session)
    refresh_household_decision_dependents(session, {"mortgage"}, cashflow=cashflow)

    event_bus.emit(
        Event(
            event_type=EventType.CASHFLOW_RECALCULATED.value,
            payload=cashflow,
        )
    )
    return {"ok": True}


@router.get("/credit-cards")
def list_cards(session: Session = Depends(get_db)):
    from app.api.routes_debts import _ensure_credit_card_from_debt
    from app.db.models import Debt

    for debt in session.exec(select(Debt)).all():
        if debt.debt_type in ("credit_card", "card"):
            _ensure_credit_card_from_debt(session, debt)
    session.commit()

    cards = list(session.exec(select(CreditCard)).all())
    seen_creditors = {c.issuer for c in cards}
    out: list[dict] = [_card_result(session, c) for c in cards]
    for debt in session.exec(select(Debt)).all():
        if debt.debt_type in ("credit_card", "card") and debt.creditor not in seen_creditors:
            limit = max(debt.balance * 1.5, debt.balance + 1000, 1000)
            pseudo = CreditCard(
                id=debt.id,
                issuer=debt.creditor,
                card_name=debt.creditor,
                balance=debt.balance,
                credit_limit=limit,
                apr=debt.apr,
                minimum_payment=debt.minimum_payment,
            )
            out.append({"card": pseudo, "analysis": analyze_credit_card(pseudo), "editable": False})
            seen_creditors.add(debt.creditor)
    return out


def _card_result(session: Session, card: CreditCard) -> dict:
    approved = _deferral_result(session, "credit_card", card.id, card.due_date)
    return {
        "card": card,
        "analysis": analyze_credit_card(card),
        "editable": True,
        "approved_deferral": approved,
        "next_due_date": approved["deferred_due_date"] if approved else (
            card.due_date.isoformat() if card.due_date else None
        ),
    }


@router.post("/credit-cards")
def create_card(data: dict, session: Session = Depends(get_db)):
    vr = validate_credit_card(data)
    raise_http_if_invalid(vr)
    payload = coerce_date_fields(data, "due_date")
    obj = CreditCard(**{k: v for k, v in payload.items() if hasattr(CreditCard, k)})
    if obj.credit_limit > 0:
        obj.utilization_percent = round(obj.balance / obj.credit_limit * 100, 1)
    session.add(obj)
    session.commit()
    session.refresh(obj)
    refresh_household_decision_dependents(session, {"card"})
    session.refresh(obj)
    event_bus.emit(Event(event_type=EventType.CREDIT_CARD_UPDATED.value, payload={"id": obj.id}))
    if obj.apr >= 20:
        event_bus.emit(Event(event_type=EventType.CREDIT_CARD_HIGH_APR.value, payload={"id": obj.id}))
    return obj


@router.put("/credit-cards/{card_id}")
def update_card(card_id: str, data: dict, session: Session = Depends(get_db)):
    obj = session.get(CreditCard, card_id)
    if not obj:
        raise HTTPException(404, "Credit card not found")
    vr = validate_credit_card(data)
    raise_http_if_invalid(vr)
    payload = coerce_date_fields(data, "due_date")
    if current_payment_deferral(session, "credit_card", card_id, obj.due_date):
        timing_fields = ("due_date", "recurrence", "minimum_payment")
        if any(key in payload and payload[key] != getattr(obj, key) for key in timing_fields):
            raise HTTPException(400, "Pay the approved one-time payment before changing its schedule")
    for k, v in payload.items():
        if hasattr(obj, k):
            setattr(obj, k, v)
    if obj.credit_limit > 0:
        obj.utilization_percent = round(obj.balance / obj.credit_limit * 100, 1)
    obj.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(obj)
    session.commit()
    session.refresh(obj)
    refresh_household_decision_dependents(session, {"card"})
    session.refresh(obj)
    event_bus.emit(Event(event_type=EventType.CREDIT_CARD_UPDATED.value, payload={"id": obj.id}))
    return {"card": obj, "analysis": analyze_credit_card(obj), "editable": True}


@router.post("/credit-cards/{card_id}/approved-deferral")
def approve_one_card_date(card_id: str, data: dict, session: Session = Depends(get_db)):
    card = session.get(CreditCard, card_id)
    if not card:
        raise HTTPException(404, "Credit card not found")
    try:
        record_approved_payment_deferral(
            session, "credit_card", card_id,
            original_due_date=date.fromisoformat(str(data.get("original_due_date") or "")),
            deferred_due_date=date.fromisoformat(str(data.get("deferred_due_date") or "")),
            payee_approved=data.get("payee_approved") is True,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    cashflow = calculate_cashflow(session)
    refresh_household_decision_dependents(session, {"card"}, cashflow=cashflow)
    return _card_result(session, card)


@router.delete("/credit-cards/{card_id}")
def delete_card(card_id: str, session: Session = Depends(get_db)):
    obj = session.get(CreditCard, card_id)
    if not obj:
        raise HTTPException(404, "Credit card not found")
    session.delete(obj)
    session.commit()
    refresh_household_decision_dependents(session, {"card"})
    cashflow = calculate_cashflow(session)

    event_bus.emit(
        Event(
            event_type=EventType.CREDIT_CARD_UPDATED.value,
            payload={"id": card_id, "deleted": True},
        )
    )
    event_bus.emit(
        Event(
            event_type=EventType.CASHFLOW_RECALCULATED.value,
            payload=cashflow,
        )
    )
    return {"ok": True}
