"""Offer intake validation helpers (no financial_snapshot imports)."""

from __future__ import annotations

from datetime import date

from sqlmodel import Session, select

from app.db.models import Bill, BorrowerProfile, CreditCard, Debt, LiquidAccount, MortgageAccount, SavingsGoal

LINKABLE_ACCOUNT_TYPES = frozenset(
    {"credit_card", "debt", "mortgage", "bill", "savings_goal"}
)

PRODUCT_ALLOWED_LINK_TYPES: dict[str, frozenset[str]] = {
    "balance_transfer": frozenset({"credit_card"}),
    "credit_card": frozenset({"credit_card"}),
    "personal_loan": frozenset({"debt", "credit_card"}),
    "auto_refinance": frozenset({"debt"}),
    "mortgage_refinance": frozenset({"mortgage"}),
    "insurance": frozenset({"bill"}),
    "utility": frozenset({"bill"}),
    "hysa": frozenset({"savings_goal"}),
    "cd": frozenset({"savings_goal"}),
}


def is_offer_expired(offer, *, today: date | None = None) -> bool:
    if offer.expiration_date is None:
        return False
    ref = today or date.today()
    return offer.expiration_date < ref


def allowed_link_types(product_type: str) -> frozenset[str]:
    return PRODUCT_ALLOWED_LINK_TYPES.get(product_type, frozenset())


def validate_offer_link(
    session: Session,
    product_type: str,
    linked_account_type: str | None,
    linked_account_id: str | None,
) -> list[str]:
    if not linked_account_type and not linked_account_id:
        return []
    if not linked_account_type or not linked_account_id:
        return ["linked_account_type and linked_account_id must both be set"]
    if linked_account_type not in LINKABLE_ACCOUNT_TYPES:
        return [f"linked_account_type must be one of: {', '.join(sorted(LINKABLE_ACCOUNT_TYPES))}"]
    allowed = allowed_link_types(product_type)
    if linked_account_type not in allowed:
        return [
            f"{product_type} must link to: {', '.join(sorted(allowed))} "
            f"(got {linked_account_type})"
        ]
    if not _entity_exists(session, linked_account_type, linked_account_id):
        return ["linked account not found"]
    return []


def _entity_exists(session: Session, account_type: str, account_id: str) -> bool:
    if account_type == "savings_goal" and account_id == "emergency":
        return session.exec(select(BorrowerProfile)).first() is not None
    if account_type == "savings_goal":
        if session.get(SavingsGoal, account_id) is not None:
            return True
        return session.get(LiquidAccount, account_id) is not None
    models = {
        "credit_card": CreditCard,
        "debt": Debt,
        "mortgage": MortgageAccount,
        "bill": Bill,
    }
    model = models.get(account_type)
    if model is None:
        return False
    return session.get(model, account_id) is not None
