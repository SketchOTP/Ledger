"""Wipe household data for a user — fresh start for onboarding."""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlmodel import Session, delete

from app.core.behavioral_store import behavioral_file_paths
from app.core.file_cabinet import cabinet_storage_root
from app.db.models import (
    Bill,
    BillInstance,
    BorrowerProfile,
    BudgetCategory,
    CfoReport,
    CreditCard,
    Debt,
    DebtOccurrence,
    DebtPayment,
    EventLogEntry,
    FinancialOffer,
    GroceryCartRun,
    GroceryCartRunItem,
    GroceryDealPriceObservation,
    GroceryItem,
    GroceryPriceObservation,
    GroceryProductMatch,
    GroceryProfile,
    GroceryStaple,
    GroceryStore,
    GroceryStoreLocation,
    IncomeSource,
    LiquidAccount,
    MortgageAccount,
    OnboardingState,
    Paycheck,
    Recommendation,
    ResearchFinding,
    ResearchIngestionFinding,
    ShoppingList,
    Transaction,
)
from app.db.user_db import clear_engine_caches, ensure_user_db, user_db_path

# Child tables before parents
_WIPE_MODELS = (
    BillInstance,
    DebtOccurrence,
    Paycheck,
    DebtPayment,
    GroceryPriceObservation,
    GroceryCartRunItem,
    GroceryCartRun,
    GroceryDealPriceObservation,
    GroceryProductMatch,
    GroceryStaple,
    GroceryProfile,
    GroceryStoreLocation,
    ShoppingList,
    Recommendation,
    CfoReport,
    EventLogEntry,
    ResearchFinding,
    ResearchIngestionFinding,
    Bill,
    IncomeSource,
    LiquidAccount,
    Debt,
    MortgageAccount,
    CreditCard,
    FinancialOffer,
    GroceryItem,
    GroceryStore,
    Transaction,
    BudgetCategory,
    BorrowerProfile,
    OnboardingState,
)


def wipe_household_session(session: Session) -> None:
    """Delete all household rows in the current user database session."""
    for model in _WIPE_MODELS:
        session.exec(delete(model))
    session.commit()


def reset_user_household_files(user_id: str) -> Path:
    """Remove the user's explicit-reset database, sidecars, and Cabinet bytes."""
    path = user_db_path(user_id)
    clear_engine_caches()
    if path.exists():
        path.unlink()
    for sidecar in behavioral_file_paths(user_id).values():
        sidecar.unlink(missing_ok=True)
    # This path is reached only by explicit whole-household reset. Product
    # retirement never calls it, so ordinary Cabinet bytes remain preserved.
    cabinet_root = cabinet_storage_root(user_id)
    if cabinet_root.is_dir():
        shutil.rmtree(cabinet_root)
    ensure_user_db(user_id)
    return path
