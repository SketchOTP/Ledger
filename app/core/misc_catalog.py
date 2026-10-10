"""Miscellaneous spending categories for My Money."""

MISC_CATEGORIES = frozenset({"daily_spending", "eating_out", "entertainment", "other"})

MISC_CATEGORY_LABELS: dict[str, str] = {
    "daily_spending": "Daily spending",
    "eating_out": "Eating out",
    "entertainment": "Entertainment",
    "other": "Other",
}


def misc_category_label(category: str) -> str:
    return MISC_CATEGORY_LABELS.get(category, category.replace("_", " ").title())


def misc_catalog() -> list[dict[str, str]]:
    return [{"id": k, "label": v} for k, v in MISC_CATEGORY_LABELS.items()]
