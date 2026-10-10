"""Research ingestion source types."""

from typing import Final

SOURCE_MORTGAGE_RATES: Final = "mortgage_rates"
SOURCE_FRED_MACRO: Final = "fred_macro_rates"
SOURCE_HYSA_RATES: Final = "hysa_rates"
SOURCE_CREDIT_CARD_BT: Final = "credit_card_balance_transfer"
SOURCE_CREDIT_CARD_BENCHMARK: Final = "credit_card_benchmark"
SOURCE_PERSONAL_LOAN_RATES: Final = "personal_loan_rates"
SOURCE_AUTO_LOAN_RATES: Final = "auto_loan_rates"
SOURCE_INSURANCE_MEDIAN: Final = "insurance_median"
SOURCE_GROCERY_PRICES: Final = "grocery_prices"
SOURCE_FOOD_CPI_TREND: Final = "food_cpi_trend"
SOURCE_UTILITY_CPI_TREND: Final = "utility_cpi_trend"
SOURCE_BLS_CPI_TREND: Final = "bls_cpi_trend"
SOURCE_EIA_UTILITY: Final = "eia_utility_benchmark"
SOURCE_USDA_FOOD_OUTLOOK: Final = "usda_food_outlook"

ALL_SOURCE_TYPES: tuple[str, ...] = (
    SOURCE_MORTGAGE_RATES,
    SOURCE_FRED_MACRO,
    SOURCE_HYSA_RATES,
    SOURCE_CREDIT_CARD_BT,
    SOURCE_CREDIT_CARD_BENCHMARK,
    SOURCE_PERSONAL_LOAN_RATES,
    SOURCE_AUTO_LOAN_RATES,
    SOURCE_INSURANCE_MEDIAN,
    SOURCE_GROCERY_PRICES,
    SOURCE_FOOD_CPI_TREND,
    SOURCE_UTILITY_CPI_TREND,
    SOURCE_BLS_CPI_TREND,
    SOURCE_EIA_UTILITY,
    SOURCE_USDA_FOOD_OUTLOOK,
)

STATUS_PENDING = "pending_review"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

# Findings older than this are flagged stale (days).
STALE_AFTER_DAYS: int = 90

# Authoritative government/API feeds — auto-approved without human review.
TRUSTED_AUTO_APPROVE_SOURCE_TYPES: frozenset[str] = frozenset(
    {
        SOURCE_MORTGAGE_RATES,
        SOURCE_FRED_MACRO,
        SOURCE_CREDIT_CARD_BENCHMARK,
        SOURCE_PERSONAL_LOAN_RATES,
        SOURCE_AUTO_LOAN_RATES,
        SOURCE_FOOD_CPI_TREND,
        SOURCE_UTILITY_CPI_TREND,
        SOURCE_BLS_CPI_TREND,
        SOURCE_EIA_UTILITY,
        SOURCE_USDA_FOOD_OUTLOOK,
    }
)

# Aggregators, promos, placeholders — require human approval before use in advice.
MANUAL_REVIEW_SOURCE_TYPES: frozenset[str] = frozenset(ALL_SOURCE_TYPES) - TRUSTED_AUTO_APPROVE_SOURCE_TYPES
