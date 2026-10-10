"""SQLModel database models."""

from datetime import UTC, date, datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def _uuid() -> str:
    return str(uuid4())


def _now() -> datetime:
    # Naive UTC for SQLite DateTime columns (matches legacy utcnow storage).
    return datetime.now(UTC).replace(tzinfo=None)


# --- Income ---


class IncomeSource(SQLModel, table=True):
    __tablename__ = "income_sources"

    id: str = Field(default_factory=_uuid, primary_key=True)
    source_name: str
    amount: float
    pay_frequency: str = "biweekly"  # weekly, biweekly, monthly, etc.
    next_pay_date: Optional[date] = None
    owner: str = "primary"
    notes: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Paycheck(SQLModel, table=True):
    __tablename__ = "paychecks"

    id: str = Field(default_factory=_uuid, primary_key=True)
    income_source_id: str = Field(foreign_key="income_sources.id")
    amount: float
    received_date: date
    notes: str = ""
    created_at: datetime = Field(default_factory=_now)


class PaymentArchiveEvent(SQLModel, table=True):
    """Permanent audit trail for mark-paid, mark-received, and restore actions."""

    __tablename__ = "payment_archive_events"

    id: str = Field(default_factory=_uuid, primary_key=True)
    action: str  # marked_paid | marked_received | restored
    kind: str  # bill | debt | mortgage | credit_card | rent | travel | misc | income
    name: str = ""
    amount: float = 0.0
    scheduled_date: date
    marked_date: Optional[date] = None
    entity_slot: str = ""
    bill_id: Optional[str] = None
    debt_id: Optional[str] = None
    mortgage_id: Optional[str] = None
    credit_card_id: Optional[str] = None
    profile_id: Optional[str] = None
    travel_expense_id: Optional[str] = None
    misc_spending_id: Optional[str] = None
    income_source_id: Optional[str] = None
    reverses_event_id: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)


# --- Bills ---


class Bill(SQLModel, table=True):
    __tablename__ = "bills"

    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str
    amount: float
    due_date: Optional[date] = None
    recurrence: str = "monthly"
    schedule_json: str = "[]"
    priority: int = 1
    autopay: bool = False
    category: str = "general"
    required: bool = True
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class BillInstance(SQLModel, table=True):
    __tablename__ = "bill_instances"

    id: str = Field(default_factory=_uuid, primary_key=True)
    bill_id: str = Field(foreign_key="bills.id")
    amount: float
    due_date: date
    paid: bool = False
    paid_date: Optional[date] = None
    created_at: datetime = Field(default_factory=_now)


class BillDeferral(SQLModel, table=True):
    """Payee-approved one-time date; original due date remains the cycle key."""

    __tablename__ = "bill_deferrals"
    __table_args__ = (UniqueConstraint("bill_id", "original_due_date", name="uq_bill_deferral_cycle"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    bill_id: str = Field(foreign_key="bills.id")
    original_due_date: date
    deferred_due_date: date
    approved_at: datetime = Field(default_factory=_now)


# --- Debts ---


class Debt(SQLModel, table=True):
    __tablename__ = "debts"

    id: str = Field(default_factory=_uuid, primary_key=True)
    creditor: str
    balance: float
    apr: float
    minimum_payment: float
    due_date: Optional[date] = None
    recurrence: str = "monthly"  # monthly | weekly | biweekly | once | none
    debt_type: str = "credit_card"
    payoff_priority: int = 1
    hardship_notes: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class DebtPayment(SQLModel, table=True):
    __tablename__ = "debt_payments"

    id: str = Field(default_factory=_uuid, primary_key=True)
    debt_id: str = Field(foreign_key="debts.id")
    amount: float
    payment_date: date
    notes: str = ""
    created_at: datetime = Field(default_factory=_now)


class DebtOccurrence(SQLModel, table=True):
    """Scheduled minimum payment marked paid for a due date (mirrors BillInstance)."""

    __tablename__ = "debt_occurrences"

    id: str = Field(default_factory=_uuid, primary_key=True)
    debt_id: str = Field(foreign_key="debts.id")
    amount: float
    due_date: date
    paid: bool = False
    paid_date: Optional[date] = None
    created_at: datetime = Field(default_factory=_now)


class RequiredPaymentDeferral(SQLModel, table=True):
    """One payee-approved date change for a non-bill required payment."""

    __tablename__ = "required_payment_deferrals"
    __table_args__ = (
        UniqueConstraint("kind", "entity_id", "original_due_date", name="uq_required_payment_deferral_cycle"),
    )

    id: str = Field(default_factory=_uuid, primary_key=True)
    kind: str  # debt | mortgage | credit_card
    entity_id: str
    original_due_date: date
    deferred_due_date: date
    approved_at: datetime = Field(default_factory=_now)


# --- Spending ---


class Transaction(SQLModel, table=True):
    __tablename__ = "transactions"

    id: str = Field(default_factory=_uuid, primary_key=True)
    amount: float
    category: str
    description: str = ""
    transaction_date: date
    planned_budget_category_id: Optional[str] = None
    planned_spending_date: Optional[date] = None
    reflected_in_checking: bool = False
    created_at: datetime = Field(default_factory=_now)


class BudgetCategory(SQLModel, table=True):
    __tablename__ = "budget_categories"

    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str
    monthly_limit: float
    usual_amount: Optional[float] = None
    period: str = "month"
    planned_weekday: int = 5  # Monday=0; Saturday is the editable default.
    spent_this_period: float = 0.0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class CashCushionContribution(SQLModel, table=True):
    """Owner-confirmed earmark of existing cash, never an automatic transfer."""

    __tablename__ = "cash_cushion_contributions"

    id: str = Field(default_factory=_uuid, primary_key=True)
    amount: float
    contribution_date: date
    created_at: datetime = Field(default_factory=_now)


# --- CFO Profile ---


class BorrowerProfile(SQLModel, table=True):
    __tablename__ = "borrower_profile"

    id: str = Field(default_factory=_uuid, primary_key=True)
    credit_score: Optional[int] = None
    annual_income: float = 0.0
    household_income: float = 0.0
    employment_type: str = "employed"
    state: str = ""
    housing_status: str = "rent"  # rent | mortgage
    monthly_rent: float = 0.0
    rent_due_date: Optional[date] = None
    estimated_dti: Optional[float] = None
    target_buffer: float = 0.0  # optional savings goal; 0 = none (cashflow uses MINIMUM_BUFFER)
    cushion_monthly_contribution: float = 100.0
    pay_period_allowance_enabled: bool = False  # explicit policy enrollment preserves other households
    cash_cushion_balance: Optional[float] = None  # earmarked portion of existing accessible cash; not another asset
    risk_tolerance: str = "moderate"
    emergency_fund_balance: float = 0.0
    checking_balance: float = 0.0
    checking_confirmed_at: Optional[datetime] = Field(default_factory=_now)
    notes: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class LiquidAccount(SQLModel, table=True):
    __tablename__ = "liquid_accounts"

    id: str = Field(default_factory=_uuid, primary_key=True)
    account_name: str
    account_type: str = "checking"  # checking | savings
    institution: str = ""
    balance: float = 0.0
    # Optional owner-known yield metadata.  This is factual account context,
    # not a recommendation or an available-money input.
    current_apy: Optional[float] = None
    apy_as_of: Optional[date] = None
    include_in_cashflow: bool = True
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class SavingsGoal(SQLModel, table=True):
    """Optional savings target — included in recommendations only when enabled and cashflow-safe."""

    __tablename__ = "savings_goals"

    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str
    target_amount: float
    saved_amount: float = 0.0
    due_date: Optional[date] = None
    enabled: bool = True
    linked_account_id: Optional[str] = Field(default=None, foreign_key="liquid_accounts.id")
    notes: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class MortgageOccurrence(SQLModel, table=True):
    """Scheduled mortgage payment marked paid for a due date."""

    __tablename__ = "mortgage_occurrences"

    id: str = Field(default_factory=_uuid, primary_key=True)
    mortgage_id: str = Field(foreign_key="mortgage_accounts.id")
    amount: float
    due_date: date
    paid: bool = False
    paid_date: Optional[date] = None
    created_at: datetime = Field(default_factory=_now)


class MortgageAccount(SQLModel, table=True):
    __tablename__ = "mortgage_accounts"

    id: str = Field(default_factory=_uuid, primary_key=True)
    lender: str
    original_balance: float
    current_balance: float
    interest_rate: float
    monthly_payment: float
    escrow_amount: float = 0.0
    term_years: int = 30
    start_date: Optional[date] = None  # legacy; synced from due_by_date when set
    due_by_date: Optional[date] = None  # pay by this date each cycle (cashflow anchor)
    late_by_date: Optional[date] = None  # grace end before considered late (AI / risk)
    recurrence: str = "monthly"  # monthly | weekly | biweekly | once | none
    remaining_term_months: Optional[int] = None
    property_value_estimate: Optional[float] = None
    loan_type: str = "conventional"
    refinance_goal: str = ""
    notes: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class CreditCardOccurrence(SQLModel, table=True):
    """Scheduled card minimum marked paid for a due date."""

    __tablename__ = "credit_card_occurrences"

    id: str = Field(default_factory=_uuid, primary_key=True)
    credit_card_id: str = Field(foreign_key="credit_cards.id")
    amount: float
    due_date: date
    paid: bool = False
    paid_date: Optional[date] = None
    created_at: datetime = Field(default_factory=_now)


class RentOccurrence(SQLModel, table=True):
    """Scheduled rent payment marked paid for a due date."""

    __tablename__ = "rent_occurrences"

    id: str = Field(default_factory=_uuid, primary_key=True)
    profile_id: str = Field(foreign_key="borrower_profile.id")
    amount: float
    due_date: date
    paid: bool = False
    paid_date: Optional[date] = None
    created_at: datetime = Field(default_factory=_now)


class CreditCard(SQLModel, table=True):
    __tablename__ = "credit_cards"

    id: str = Field(default_factory=_uuid, primary_key=True)
    issuer: str
    card_name: str
    balance: float
    credit_limit: float
    apr: float
    minimum_payment: float
    due_date: Optional[date] = None
    recurrence: str = "monthly"  # monthly | weekly | biweekly | once | none
    rewards_type: str = ""
    annual_fee: float = 0.0
    promo_apr_end_date: Optional[date] = None
    utilization_percent: Optional[float] = None
    notes: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class FinancialOffer(SQLModel, table=True):
    __tablename__ = "financial_offers"

    id: str = Field(default_factory=_uuid, primary_key=True)
    provider: str
    product_type: str
    offer_name: str = ""
    estimated_apr: Optional[float] = None
    apy: Optional[float] = None
    term: str = ""
    term_months: Optional[int] = None
    monthly_payment_estimate: Optional[float] = None
    balance_transfer_fee_percent: Optional[float] = None
    annual_fee: float = 0.0
    origination_fee: Optional[float] = None
    closing_costs: Optional[float] = None
    fees: float = 0.0
    eligibility_notes: str = ""
    credit_score_requirement: Optional[int] = None
    dti_requirement: Optional[float] = None
    source_url: str = ""
    evidence_notes: str = ""
    expiration_date: Optional[date] = None
    requires_credit_check: bool = True
    notes: str = ""
    linked_account_id: Optional[str] = None
    linked_account_type: Optional[str] = None  # credit_card | debt | mortgage | bill | savings_goal
    intake_source: str = "research"  # research | user_intake
    intake_status: str = "approved"  # pending_review | approved | rejected
    fetched_at: datetime = Field(default_factory=_now)
    confidence: float = 0.5
    recommendation_status: str = "pending"
    estimated_savings: float = 0.0
    warnings: str = ""  # pipe-separated
    comparison_json: str = "{}"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# --- Grocery ---


class GroceryStore(SQLModel, table=True):
    __tablename__ = "grocery_stores"

    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str
    distance_miles: float = 0.0
    notes: str = ""
    created_at: datetime = Field(default_factory=_now)


class GroceryItem(SQLModel, table=True):
    __tablename__ = "grocery_items"

    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str
    preferred_brand: str = ""
    package_size: str = ""
    weekly_budget: float = 0.0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class GroceryPriceObservation(SQLModel, table=True):
    __tablename__ = "grocery_price_observations"

    id: str = Field(default_factory=_uuid, primary_key=True)
    item_id: str = Field(foreign_key="grocery_items.id")
    store_id: str = Field(foreign_key="grocery_stores.id")
    unit_price: float
    sale_price: Optional[float] = None
    observed_date: date
    source: str = "manual"
    confidence: float = 1.0
    created_at: datetime = Field(default_factory=_now)


class ShoppingList(SQLModel, table=True):
    __tablename__ = "shopping_lists"

    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str = "Weekly"
    item_ids: str = ""  # comma-separated item ids
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class GroceryProfile(SQLModel, table=True):
    """Household grocery preferences for deal optimizer."""

    __tablename__ = "grocery_profiles"

    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str = "Household"
    zip_code: str = ""
    preferred_store: str = ""  # kroger | walmart | none
    default_kroger_location_id: str = ""
    default_walmart_store_id: str = ""
    fulfillment_mode: str = "pickup"  # pickup | delivery | in_store
    budget_limit: Optional[float] = None
    prefer_store_brand: bool = True
    allow_substitutions: bool = True
    allow_walmart: bool = True
    allow_kroger: bool = True
    max_stores_per_trip: int = 1
    minimum_savings_to_split_stores: float = 10.0
    extra_store_penalty_amount: float = 5.0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class GroceryStaple(SQLModel, table=True):
    """Recurring household grocery items for deal scanning."""

    __tablename__ = "grocery_staples"

    id: str = Field(default_factory=_uuid, primary_key=True)
    profile_id: str = Field(foreign_key="grocery_profiles.id")
    name: str
    normalized_name: str = ""
    category: str = ""
    preferred_brand: str = ""
    preferred_size: str = ""
    quantity_default: float = 1.0
    unit: str = "each"
    must_match_brand: bool = False
    allow_generic: bool = True
    allow_size_variance: bool = True
    allow_substitutions: bool = True
    notes: str = ""
    is_active: bool = True
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class GroceryStoreLocation(SQLModel, table=True):
    """Retailer store locations from provider APIs."""

    __tablename__ = "grocery_store_locations"

    id: str = Field(default_factory=_uuid, primary_key=True)
    provider: str  # kroger | walmart
    provider_store_id: str
    name: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""
    chain: str = ""
    supports_pickup: bool = True
    supports_delivery: bool = False
    supports_cart_add: bool = False
    last_checked_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class GroceryProductMatch(SQLModel, table=True):
    """Pinned/rejected product matches per staple."""

    __tablename__ = "grocery_product_matches"

    id: str = Field(default_factory=_uuid, primary_key=True)
    grocery_staple_id: str = Field(foreign_key="grocery_staples.id")
    provider: str
    provider_store_id: str = ""
    provider_product_id: str = ""
    upc: str = ""
    product_name: str = ""
    brand: str = ""
    size: str = ""
    confidence_score: float = 0.0
    is_pinned: bool = False
    is_rejected: bool = False
    rejection_reason: str = ""
    last_seen_price: Optional[float] = None
    last_seen_promo_price: Optional[float] = None
    last_checked_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class GroceryDealPriceObservation(SQLModel, table=True):
    """Provider-sourced price observations from deal scans."""

    __tablename__ = "grocery_deal_price_observations"

    id: str = Field(default_factory=_uuid, primary_key=True)
    grocery_staple_id: str = Field(foreign_key="grocery_staples.id")
    provider: str
    provider_store_id: str = ""
    provider_product_id: str = ""
    upc: str = ""
    regular_price: Optional[float] = None
    promo_price: Optional[float] = None
    effective_price: Optional[float] = None
    unit_price: Optional[float] = None
    unit_price_basis: str = ""
    size: str = ""
    availability: str = ""
    fulfillment_mode: str = ""
    observed_at: datetime = Field(default_factory=_now)
    raw_payload_json: str = "{}"


class GroceryCartRun(SQLModel, table=True):
    """Optimizer cart run — draft through pushed."""

    __tablename__ = "grocery_cart_runs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    profile_id: str = Field(foreign_key="grocery_profiles.id")
    status: str = "draft"  # draft|reviewed|approved|pushed_partial|pushed_complete|failed|cancelled
    strategy: str = ""  # single_store_kroger|single_store_walmart|split_store_absolute_cheapest|split_store_practical
    estimated_total: float = 0.0
    estimated_regular_total: float = 0.0
    estimated_savings: float = 0.0
    store_count: int = 0
    recommendation_summary: str = ""
    scan_batch_id: str = ""
    scan_recommendation: str = ""
    created_at: datetime = Field(default_factory=_now)
    approved_at: Optional[datetime] = None
    pushed_at: Optional[datetime] = None


class GroceryCartRunItem(SQLModel, table=True):
    """Line items in a cart run."""

    __tablename__ = "grocery_cart_run_items"

    id: str = Field(default_factory=_uuid, primary_key=True)
    cart_run_id: str = Field(foreign_key="grocery_cart_runs.id")
    grocery_staple_id: str = Field(foreign_key="grocery_staples.id")
    requested_name: str = ""
    provider: str = ""
    provider_store_id: str = ""
    selected_provider_product_id: str = ""
    selected_upc: str = ""
    selected_product_name: str = ""
    brand: str = ""
    size: str = ""
    quantity: float = 1.0
    regular_price: Optional[float] = None
    promo_price: Optional[float] = None
    effective_price: Optional[float] = None
    unit_price: Optional[float] = None
    unit_price_basis: str = ""
    reason_selected: str = ""
    confidence_score: float = 0.0
    substitution_type: str = "exact"
    cart_add_supported: bool = False
    cart_add_status: str = "pending"  # pending|success|failed|skipped|link_only
    cart_add_error: str = ""
    raw_payload_json: str = "{}"
    alternative_candidates_json: str = "[]"


# --- Travel ---


class TravelSettings(SQLModel, table=True):
    """Which transport modes the household tracks (single row per user DB)."""

    __tablename__ = "travel_settings"

    id: str = Field(default="default", primary_key=True)
    use_car: bool = False
    use_bus: bool = False
    use_boat: bool = False
    use_plane: bool = False
    updated_at: datetime = Field(default_factory=_now)


class TravelOccurrence(SQLModel, table=True):
    """Scheduled travel expense marked paid for a due date."""

    __tablename__ = "travel_occurrences"

    id: str = Field(default_factory=_uuid, primary_key=True)
    travel_expense_id: str = Field(foreign_key="travel_expenses.id")
    amount: float
    due_date: date
    paid: bool = False
    paid_date: Optional[date] = None
    created_at: datetime = Field(default_factory=_now)


class MiscOccurrence(SQLModel, table=True):
    """Scheduled misc spending marked paid for a due date."""

    __tablename__ = "misc_occurrences"

    id: str = Field(default_factory=_uuid, primary_key=True)
    misc_spending_id: str = Field(foreign_key="misc_spending.id")
    amount: float
    due_date: date
    paid: bool = False
    paid_date: Optional[date] = None
    created_at: datetime = Field(default_factory=_now)


class TravelExpense(SQLModel, table=True):
    __tablename__ = "travel_expenses"

    id: str = Field(default_factory=_uuid, primary_key=True)
    mode: str  # car | bus | boat | plane
    expense_type: str  # catalog slug, e.g. gas, bus_pass, airfare
    label: str = ""  # optional override display name
    amount: float
    due_date: Optional[date] = None
    recurrence: str = "monthly"  # monthly | weekly | biweekly | once | none
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class MiscSpending(SQLModel, table=True):
    """Discretionary / variable spending (eating out, entertainment, etc.)."""

    __tablename__ = "misc_spending"

    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str
    category: str = "other"  # daily_spending | eating_out | entertainment | other
    amount: float
    due_date: Optional[date] = None
    recurrence: str = "monthly"  # monthly | weekly | biweekly | once | none
    active: bool = True
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# --- Reports & Recommendations ---


class CfoReport(SQLModel, table=True):
    __tablename__ = "cfo_reports"

    id: str = Field(default_factory=_uuid, primary_key=True)
    report_json: str
    cash_status: str = "unknown"
    generated_at: datetime = Field(default_factory=_now)
    ai_summary: str = ""


class Recommendation(SQLModel, table=True):
    __tablename__ = "recommendations"

    id: str = Field(default_factory=_uuid, primary_key=True)
    title: str
    action: str
    action_type: str = "hold_cash"
    category: str = "cashflow"
    priority: int = 1
    risk_level: str = "low"
    expected_impact: str = ""
    why: str = ""
    assumptions: str = "[]"  # JSON array
    blocked_by: str = "[]"
    confidence: float = 0.8
    source: str = "deterministic"
    estimated_monthly_savings: float = 0.0
    estimated_total_savings: float = 0.0
    effort_level: str = "low"
    requires_manual_verification: bool = False
    math_json: str = "{}"
    sources_json: str = "[]"
    audit_json: str = "{}"
    scores_json: str = "{}"
    status: str = "active"  # active, dismissed, accepted, snoozed
    snooze_until: Optional[date] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class FinancialAction(SQLModel, table=True):
    """Tracked execution of a recommendation or urgent offer follow-up."""

    __tablename__ = "financial_actions"

    id: str = Field(default_factory=_uuid, primary_key=True)
    recommendation_id: Optional[str] = Field(default=None, foreign_key="recommendations.id")
    offer_id: Optional[str] = Field(default=None, foreign_key="financial_offers.id")
    opportunity_candidate_id: Optional[str] = Field(default=None, foreign_key="opportunity_candidates.id", index=True)
    title: str
    action_type: str = "call_provider"
    status: str = "open"  # open | in_progress | completed | dismissed | failed
    priority: int = 50
    due_date: Optional[date] = None
    expected_monthly_savings: float = 0.0
    expected_total_savings: float = 0.0
    actual_monthly_savings: Optional[float] = None
    actual_total_savings: Optional[float] = None
    linked_entity_type: str = ""
    linked_entity_id: str = ""
    evidence_id: Optional[str] = Field(default=None, foreign_key="evidence_files.id")
    notes: str = ""
    dismiss_reason: str = ""
    is_urgent: bool = False
    blocked_by: str = "[]"  # JSON array
    created_at: datetime = Field(default_factory=_now)
    completed_at: Optional[datetime] = None


# --- V2 capabilities and opportunities ---


class CapabilityProfile(SQLModel, table=True):
    """Owner-controlled limits and preferences; never overrides money-state safety."""

    __tablename__ = "capability_profiles"

    id: str = Field(default="default", primary_key=True)
    max_setup_hours: Optional[float] = None
    max_recurring_hours_per_week: Optional[float] = None
    preferred_effort_level: str = ""
    customer_interaction_willingness: Optional[bool] = None
    sales_outreach_willingness: Optional[bool] = None
    physical_labor_willingness: Optional[bool] = None
    prefers_async: bool = False
    prefers_automated: bool = False
    prefers_low_touch: bool = False
    prefers_recurring_income: bool = False
    prefers_scalable_income: bool = False
    allows_one_time_income: bool = True
    desired_time_to_value_days: Optional[int] = None
    minimum_meaningful_payoff: Optional[float] = None
    max_preferred_capital: Optional[float] = None
    refused_activities_json: str = "[]"
    notes: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class CapabilitySkill(SQLModel, table=True):
    __tablename__ = "capability_skills"

    id: str = Field(default_factory=_uuid, primary_key=True)
    profile_id: str = Field(default="default", foreign_key="capability_profiles.id", index=True)
    name: str
    category: str = "other"
    proficiency: float = 0.5
    monetization_willingness: bool = True
    notes: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class CapabilityAsset(SQLModel, table=True):
    __tablename__ = "capability_assets"

    id: str = Field(default_factory=_uuid, primary_key=True)
    profile_id: str = Field(default="default", foreign_key="capability_profiles.id", index=True)
    name: str
    asset_type: str = "other"
    availability: str = "available"
    monetization_willingness: bool = True
    estimated_reuse_effort_hours: Optional[float] = None
    notes: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class HouseholdProfile(SQLModel, table=True):
    """Privacy-minimized household resilience preferences and context."""

    __tablename__ = "household_profiles"

    id: str = Field(default="default", primary_key=True)
    risk_tolerance: str = "moderate"  # conservative | moderate | growth
    target_reserve_months: Optional[float] = None
    desired_growth_share: Optional[float] = None
    desired_free_share: Optional[float] = None
    dependents_count: int = 0
    income_notes: str = ""
    planning_notes: str = ""
    dedicated_resilience_balance: float = 0.0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class HouseholdRisk(SQLModel, table=True):
    """Owner-entered coarse risk facts; sensitive narrative is not required."""

    __tablename__ = "household_risks"

    id: str = Field(default_factory=_uuid, primary_key=True)
    profile_id: str = Field(default="default", foreign_key="household_profiles.id", index=True)
    risk_type: str
    severity: str = "moderate"  # low | moderate | high
    monthly_impact: Optional[float] = None
    duration_months: Optional[int] = None
    active: bool = True
    notes: str = ""
    likelihood_band: str = "unknown"
    impact_low: Optional[float] = None
    impact_high: Optional[float] = None
    income_impact_percent: Optional[float] = None
    horizon_months: Optional[int] = None
    mitigation: str = ""
    provenance: str = "owner_entered"
    confidence: str = "unknown"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class HouseholdLifeEvent(SQLModel, table=True):
    """Known future event represented without member-specific private detail."""

    __tablename__ = "household_life_events"

    id: str = Field(default_factory=_uuid, primary_key=True)
    profile_id: str = Field(default="default", foreign_key="household_profiles.id", index=True)
    event_type: str
    event_date: Optional[date] = None
    estimated_cost: Optional[float] = None
    estimated_cost_low: Optional[float] = None
    estimated_cost_high: Optional[float] = None
    income_impact: Optional[float] = None
    duration_months: Optional[int] = None
    certainty: str = "possible"  # expected | possible | unknown
    notes: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class HouseholdCoverage(SQLModel, table=True):
    __tablename__ = "household_coverages"

    id: str = Field(default_factory=_uuid, primary_key=True)
    profile_id: str = Field(default="default", foreign_key="household_profiles.id", index=True)
    coverage_type: str
    status: str = "unknown"  # adequate | partial | none | unknown
    monthly_cost: Optional[float] = None
    deductible: Optional[float] = None
    out_of_pocket_max: Optional[float] = None
    income_replacement_percent: Optional[float] = None
    waiting_period_days: Optional[int] = None
    coverage_gap: Optional[float] = None
    notes: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class HouseholdVehicle(SQLModel, table=True):
    __tablename__ = "household_vehicles"

    id: str = Field(default_factory=_uuid, primary_key=True)
    profile_id: str = Field(default="default", foreign_key="household_profiles.id", index=True)
    vehicle_type: str = "vehicle"
    replacement_value: Optional[float] = None
    monthly_transport_cost: Optional[float] = None
    year: Optional[int] = None
    make: str = ""
    model: str = ""
    mileage: Optional[int] = None
    required_for_income: bool = False
    alternate_transport: bool = False
    backup_vehicle: bool = False
    warranty: bool = False
    deductible: Optional[float] = None
    repair_exposure_low: Optional[float] = None
    repair_exposure_high: Optional[float] = None
    notes: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class HouseholdAdvisorySnapshot(SQLModel, table=True):
    """Durable derived advisory output; authoritative dollars remain D001."""

    __tablename__ = "household_advisory_snapshots"

    id: str = Field(default_factory=_uuid, primary_key=True)
    snapshot_key: str = Field(default="current", index=True)
    resilience_json: str = "{}"
    wealth_plan_json: str = "{}"
    hypotheses_json: str = "[]"
    provenance_json: str = "{}"
    generated_at: datetime = Field(default_factory=_now)


class HouseholdMember(SQLModel, table=True):
    __tablename__ = "household_members"

    id: str = Field(default_factory=_uuid, primary_key=True)
    profile_id: str = Field(default="default", foreign_key="household_profiles.id", index=True)
    label: str
    age_band: str = "adult"
    is_adult: bool = True
    financially_dependent: bool = False
    income_contributor: bool = False
    linked_income_source_id: Optional[str] = Field(default=None, foreign_key="income_sources.id")
    recurring_care_cost: Optional[float] = None
    expected_income_interruption_months: Optional[int] = None
    active: bool = True
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class HouseholdIncomeRisk(SQLModel, table=True):
    __tablename__ = "household_income_risks"

    id: str = Field(default_factory=_uuid, primary_key=True)
    profile_id: str = Field(default="default", foreign_key="household_profiles.id", index=True)
    income_source_id: Optional[str] = Field(default=None, foreign_key="income_sources.id")
    income_share: Optional[float] = None
    income_type: str = "fixed"  # fixed | variable | seasonal
    stability_band: str = "unknown"
    known_reduction: Optional[float] = None
    affected_percent: Optional[float] = None
    recovery_duration_months: Optional[int] = None
    severance_or_benefit_offset: Optional[float] = None
    active: bool = True
    notes: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class OpportunityCandidate(SQLModel, table=True):
    """Normalized durable V2 candidate. Unknown economics deliberately remain NULL."""

    __tablename__ = "opportunity_candidates"

    id: str = Field(default_factory=_uuid, primary_key=True)
    identity_key: str = Field(index=True)
    title: str
    summary: str = ""
    category: str = "other"
    source_type: str = "owner_supplied"
    source_url: str = ""
    source_label: str = ""
    verification_status: str = "unverified"
    freshness_status: str = "unknown"
    discovered_at: datetime = Field(default_factory=_now)
    fetched_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    expected_gross_low: Optional[float] = None
    expected_gross_high: Optional[float] = None
    estimated_direct_cost: Optional[float] = None
    expected_net_low: Optional[float] = None
    expected_net_high: Optional[float] = None
    probability_low: Optional[float] = None
    probability_high: Optional[float] = None
    required_capital: Optional[float] = None
    capital_at_risk: Optional[float] = None
    recurring_operating_cost: Optional[float] = None
    time_to_value_days: Optional[int] = None
    value_horizon_days: Optional[int] = None
    setup_hours: Optional[float] = None
    recurring_hours_per_week: Optional[float] = None
    continued_labor_dependence: float = 0.0
    automation_potential: float = 0.0
    scalability: float = 0.0
    repeatability: float = 0.0
    reversibility: float = 0.0
    liquidity_lockup_days: Optional[int] = None
    complexity: float = 0.0
    downside_severity: float = 0.0
    matched_skills_json: str = "[]"
    matched_assets_json: str = "[]"
    violated_constraints_json: str = "[]"
    required_activities_json: str = "[]"
    experiment_json: str = "{}"
    capability_fit_score: float = 0.0
    evidence_json: str = "[]"
    assumptions_json: str = "[]"
    qualification_status: str = "discovered"
    rejection_reasons_json: str = "[]"
    safety_eligible: bool = False
    score_components_json: str = "{}"
    rank_score: Optional[float] = None
    scoring_version: str = "v2.1"
    lifecycle_status: str = "discovered"
    actual_net_value: Optional[float] = None
    actual_setup_hours: Optional[float] = None
    actual_recurring_hours_per_week: Optional[float] = None
    actual_time_to_value_days: Optional[int] = None
    actual_capital_lost: Optional[float] = None
    actual_capital_returned: Optional[float] = None
    outcome_failure_reason: str = ""
    outcome_notes: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class OpportunityValidationFact(SQLModel, table=True):
    """Versioned owner facts used to complete candidate economics.

    Facts are deliberately separate from candidates and external evidence:
    saving a fact cannot directly set a score, lifecycle, or safety result.
    """

    __tablename__ = "opportunity_validation_facts"

    id: str = Field(default_factory=_uuid, primary_key=True)
    candidate_id: str = Field(foreign_key="opportunity_candidates.id", index=True)
    fact_type: str = Field(index=True)
    factual_value_json: str = "null"
    units: str = ""
    source_type: str = "owner_entered"
    source_label: str = ""
    observed_at: datetime = Field(default_factory=_now)
    expires_at: Optional[datetime] = None
    verification_status: str = "owner_entered"
    supersedes_id: Optional[str] = None
    status: str = "current"  # current | superseded
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class OpportunityTransition(SQLModel, table=True):
    __tablename__ = "opportunity_transitions"

    id: str = Field(default_factory=_uuid, primary_key=True)
    candidate_id: str = Field(foreign_key="opportunity_candidates.id", index=True)
    from_status: str = ""
    to_status: str
    reason: str = ""
    evidence_json: str = "[]"
    created_at: datetime = Field(default_factory=_now)


class OpportunityResearchRun(SQLModel, table=True):
    """Bounded, privacy-sanitized D003 external discovery audit record."""

    __tablename__ = "opportunity_research_runs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    trigger: str = "manual"  # manual | scheduler | revalidation
    scope: str = "all"
    status: str = "running"  # running | success | partial | failed
    brief_json: str = "{}"
    plan_json: str = "{}"
    provider_status_json: str = "[]"
    error_json: str = "[]"
    results_examined: int = 0
    candidates_created: int = 0
    candidates_merged: int = 0
    candidates_prefiltered: int = 0
    candidates_qualified: int = 0
    top_moves_changed: bool = False
    request_count: int = 0
    # A hashed, coarse state marker. It lets refreshes explain why a run was
    # reconsidered without retaining or transmitting household balances.
    material_state_fingerprint: str = ""
    started_at: datetime = Field(default_factory=_now, index=True)
    completed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class OpportunityEvidence(SQLModel, table=True):
    """Minimal claim-level provenance for externally researched candidates."""

    __tablename__ = "opportunity_evidence"

    id: str = Field(default_factory=_uuid, primary_key=True)
    research_run_id: str = Field(foreign_key="opportunity_research_runs.id", index=True)
    candidate_id: Optional[str] = Field(default=None, foreign_key="opportunity_candidates.id", index=True)
    provider_id: str = Field(index=True)
    source_url: str
    source_title: str = ""
    source_domain: str = ""
    publisher: str = ""
    source_quality: str = "unknown"
    claim_type: str
    factual_value_json: str = "null"
    units: str = ""
    evidence_strength: float = 0.0
    extraction_method: str = "structured"
    supporting_snippet: str = ""
    content_fingerprint: str = ""
    retrieved_at: datetime = Field(default_factory=_now)
    published_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    status: str = "current"  # current | stale | expired
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class OpportunityPredictionSnapshot(SQLModel, table=True):
    """Immutable decision-time prediction for a V2 candidate action."""

    __tablename__ = "opportunity_prediction_snapshots"

    id: str = Field(default_factory=_uuid, primary_key=True)
    candidate_id: str = Field(foreign_key="opportunity_candidates.id", index=True)
    financial_action_id: Optional[str] = Field(default=None, foreign_key="financial_actions.id", index=True)
    scoring_version: str = "v2.2"
    prediction_json: str = "{}"
    created_at: datetime = Field(default_factory=_now)


class OpportunityOutcomeObservation(SQLModel, table=True):
    """Actual V2 outcome facts; NULL deliberately means not observed."""

    __tablename__ = "opportunity_outcome_observations"

    id: str = Field(default_factory=_uuid, primary_key=True)
    candidate_id: str = Field(foreign_key="opportunity_candidates.id", index=True)
    financial_action_id: Optional[str] = Field(default=None, foreign_key="financial_actions.id", index=True)
    outcome_status: str = "in_progress"  # in_progress | completed | failed | abandoned
    actual_gross_value: Optional[float] = None
    actual_net_value: Optional[float] = None
    actual_direct_cost: Optional[float] = None
    actual_capital_committed: Optional[float] = None
    actual_capital_lost: Optional[float] = None
    actual_capital_returned: Optional[float] = None
    actual_setup_hours: Optional[float] = None
    actual_recurring_hours_per_week: Optional[float] = None
    first_value_date: Optional[date] = None
    actual_time_to_value_days: Optional[int] = None
    completed_at: Optional[datetime] = None
    outcome_reason: str = ""
    outcome_notes: str = ""
    evidence_reference: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class OpportunityCalibrationAudit(SQLModel, table=True):
    """Auditable bounded V2 learning adjustment, never a safety override."""

    __tablename__ = "opportunity_calibration_audits"

    id: str = Field(default_factory=_uuid, primary_key=True)
    dimension: str = Field(index=True)
    dimension_value: str = Field(index=True)
    sample_size: int = 0
    confidence: float = 0.0
    score_adjustment: float = 0.0
    learning_version: str = "v2.4"
    profile_json: str = "{}"
    reason: str = ""
    created_at: datetime = Field(default_factory=_now)


class ResearchFinding(SQLModel, table=True):
    __tablename__ = "research_findings"

    id: str = Field(default_factory=_uuid, primary_key=True)
    product_type: str
    finding_json: str
    source: str = ""
    confidence: float = 0.5
    created_at: datetime = Field(default_factory=_now)


class EvidenceFile(SQLModel, table=True):
    __tablename__ = "evidence_files"

    id: str = Field(default_factory=_uuid, primary_key=True)
    filename: str
    content_type: str = "application/octet-stream"
    sha256: str
    size_bytes: int
    evidence_type: str
    linked_entity_type: str = ""
    linked_entity_id: str = ""
    source_url: str = ""
    storage_path: str = ""
    uploaded_at: datetime = Field(default_factory=_now)
    extracted_text: str = ""
    status: str = "pending_review"  # pending_review | approved | rejected
    reviewed_at: Optional[datetime] = None
    reviewer_notes: str = ""
    created_at: datetime = Field(default_factory=_now)


class EvidenceExtraction(SQLModel, table=True):
    __tablename__ = "evidence_extractions"

    id: str = Field(default_factory=_uuid, primary_key=True)
    evidence_id: str = Field(foreign_key="evidence_files.id")
    extraction_type: str
    status: str = "draft"  # draft | needs_review | failed | approved | rejected
    fields_json: str = "{}"
    line_items_json: str = "[]"
    confidence_json: str = "{}"
    warnings_json: str = "[]"
    suggestions_json: str = "[]"
    reviewed_at: Optional[datetime] = None
    reviewer_notes: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ResearchIngestionFinding(SQLModel, table=True):
    """Fetched research awaiting human approval before affecting recommendations."""

    __tablename__ = "research_ingestion_findings"

    id: str = Field(default_factory=_uuid, primary_key=True)
    source_type: str
    status: str = "pending_review"  # pending_review | approved | rejected
    source_url: str
    finding_json: str
    comparison_json: str = "{}"
    fetched_at: datetime = Field(default_factory=_now)
    reviewed_at: Optional[datetime] = None
    reviewer_notes: str = ""
    is_stale: bool = False
    primary_value: Optional[float] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class OnboardingState(SQLModel, table=True):
    """Tracks wizard progress and user declarations (no mortgage, etc.)."""

    __tablename__ = "onboarding_state"

    id: str = Field(default="default", primary_key=True)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    steps_completed_json: str = "[]"
    declarations_json: str = "{}"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class OutcomeLearningAudit(SQLModel, table=True):
    """Audit trail for deterministic outcome-based ranking adjustments."""

    __tablename__ = "outcome_learning_audits"

    id: str = Field(default_factory=_uuid, primary_key=True)
    action_type: str
    sample_size: int = 0
    score_adjustment: float = 0.0
    confidence: float = 0.0
    profile_json: str = "{}"
    reason: str = ""
    created_at: datetime = Field(default_factory=_now)


class FinancialSnapshotRecord(SQLModel, table=True):
    """Persisted periodic household financial metrics for timeline analysis."""

    __tablename__ = "financial_snapshots"

    id: str = Field(default_factory=_uuid, primary_key=True)
    cadence: str = "daily"  # daily | weekly | monthly
    snapshot_date: date
    cashflow_json: str = "{}"
    debts_json: str = "{}"
    dti_json: str = "{}"
    credit_util_json: str = "{}"
    mortgage_json: str = "[]"
    grocery_pressure: float = 0.0
    recommendation_count: int = 0
    pressure_score: float = 0.0
    data_quality_score: float = 0.0
    metrics_json: str = "{}"
    source_snapshot_id: str = ""
    created_at: datetime = Field(default_factory=_now)


class FinancialTimelineEvent(SQLModel, table=True):
    """Derived financial change events with provenance."""

    __tablename__ = "financial_timeline_events"

    id: str = Field(default_factory=_uuid, primary_key=True)
    event_type: str
    title: str
    description: str = ""
    severity: str = "info"  # info | warning | critical
    occurred_at: date
    provenance_json: str = "{}"
    payload_json: str = "{}"
    created_at: datetime = Field(default_factory=_now)


class FinancialMemoryPattern(SQLModel, table=True):
    """Detected repeating financial patterns (deterministic, provenance-backed)."""

    __tablename__ = "financial_memory_patterns"

    id: str = Field(default_factory=_uuid, primary_key=True)
    pattern_type: str
    status: str = "active"  # active | resolved
    title: str
    description: str = ""
    provenance_json: str = "{}"
    confidence: float = 0.0
    first_detected_at: datetime = Field(default_factory=_now)
    last_seen_at: datetime = Field(default_factory=_now)
    resolved_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_now)


class ScheduledJob(SQLModel, table=True):
    """Local scheduled maintenance jobs — advisory automation only."""

    __tablename__ = "scheduled_jobs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    job_type: str = Field(index=True)
    enabled: bool = True
    cadence: str = "daily"  # hourly | daily | weekly
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    last_status: str = "idle"  # idle | running | success | failed | skipped
    last_error: str = ""
    config_json: str = "{}"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class PushSubscription(SQLModel, table=True):
    """Browser Web Push subscription for a user device."""

    __tablename__ = "push_subscriptions"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(index=True)
    endpoint: str = Field(index=True)
    p256dh_key: str
    auth_key: str
    user_agent: str = ""
    device_label: str = ""
    enabled: bool = True
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    last_success_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None
    failure_count: int = 0


class NotificationDeliveryAttempt(SQLModel, table=True):
    """Audit log for outbound notification delivery (Web Push, etc.)."""

    __tablename__ = "notification_delivery_attempts"

    id: str = Field(default_factory=_uuid, primary_key=True)
    notification_id: str = Field(index=True)
    push_subscription_id: str = Field(index=True, default="")
    channel: str = "web_push"
    status: str = "pending"  # success | failed | stale_subscription
    provider_status_code: int = 0
    error_message: str = ""
    attempted_at: datetime = Field(default_factory=_now)


class NotificationInbox(SQLModel, table=True):
    """Dashboard inbox alerts — advisory only, no autonomous actions."""

    __tablename__ = "notification_inbox"

    id: str = Field(default_factory=_uuid, primary_key=True)
    notification_type: str
    channel: str = "dashboard_inbox"
    severity: str = "info"  # info | warning | critical
    title: str
    body: str = ""
    link_path: str = "/"
    dedupe_key: str = Field(index=True)
    status: str = "active"  # active | dismissed | snoozed
    snoozed_until: Optional[datetime] = None
    dismiss_reason: str = ""
    entity_type: str = ""
    entity_id: str = ""
    payload_json: str = "{}"
    escalated: bool = False
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class EventLogEntry(SQLModel, table=True):
    __tablename__ = "event_log"

    id: str = Field(default_factory=_uuid, primary_key=True)
    event_id: str
    event_type: str
    created_at: datetime = Field(default_factory=_now)
    source: str = "api"
    payload_json: str = "{}"
    correlation_id: str = ""
    severity: str = "info"


class OpportunityDailyCache(SQLModel, table=True):
    """Latest daily opportunity scan — refreshed once per calendar day."""

    __tablename__ = "opportunity_daily_cache"

    id: str = Field(default="current", primary_key=True)
    opportunities_json: str = "[]"
    web_sources_json: str = "[]"
    refreshed_at: datetime = Field(default_factory=_now)


class OpportunityUserState(SQLModel, table=True):
    """User save/dismiss actions on opportunities."""

    __tablename__ = "opportunity_user_state"

    opportunity_key: str = Field(primary_key=True)
    status: str = "saved"  # saved | dismissed
    title: str = ""
    category: str = ""
    payload_json: str = "{}"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class FileCabinetFolder(SQLModel, table=True):
    __tablename__ = "file_cabinet_folders"

    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str
    created_at: datetime = Field(default_factory=_now)


class FileCabinetDocument(SQLModel, table=True):
    __tablename__ = "file_cabinet_documents"

    id: str = Field(default_factory=_uuid, primary_key=True)
    folder_id: str = Field(foreign_key="file_cabinet_folders.id")
    title: str
    doc_type: str = "file"  # file | note
    tags_json: str = "[]"
    file_path: str = ""
    mime_type: str = ""
    note_text: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
