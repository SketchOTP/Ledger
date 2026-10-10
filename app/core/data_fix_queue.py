"""Actionable data-fix queue from data quality assessment."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.core.data_quality import assess_data_quality

# Map data-quality check codes to human labels and fix destinations.
CHECK_META: dict[str, dict[str, str]] = {
    "missing_apr": {
        "label": "Missing APR",
        "fix_path": "/money?section=credit_cards",
        "fix_hint": "Enter APR on the credit card",
    },
    "missing_required_fields": {
        "label": "Missing required fields",
        "fix_path": "/money",
        "fix_hint": "Complete required fields",
    },
    "missing_due_date": {
        "label": "Missing due date",
        "fix_path": "/money?section=bills",
        "fix_hint": "Set bill due date",
    },
    "missing_source_url": {
        "label": "Missing source URL",
        "fix_path": "/research-admin",
        "fix_hint": "Add source URL to research entry",
    },
    "missing_last_checked": {
        "label": "Stale or missing rate date",
        "fix_path": "/research-admin",
        "fix_hint": "Add as_of / last_checked date",
    },
    "stale_values": {
        "label": "Stale value",
        "fix_path": "/money",
        "fix_hint": "Update outdated amount or date",
    },
    "unverified_manual_entry": {
        "label": "Unverified entry",
        "fix_path": "/money",
        "fix_hint": "Verify and update the entry",
    },
    "impossible_values": {
        "label": "Impossible value",
        "fix_path": "/money",
        "fix_hint": "Correct the invalid number",
    },
    "conflicting_values": {
        "label": "Conflicting values",
        "fix_path": "/money",
        "fix_hint": "Reconcile conflicting amounts",
    },
    "missing_mortgage_market_rate": {
        "label": "Missing mortgage market rate",
        "fix_path": "/research-admin",
        "fix_hint": "Approve mortgage rate research",
    },
    "low_source_confidence": {
        "label": "Low research confidence",
        "fix_path": "/research-admin",
        "fix_hint": "Review research source",
    },
}

DOMAIN_PATHS: dict[str, str] = {
    "income": "/money?section=income",
    "bills": "/money?section=bills",
    "debts": "/money?section=debts",
    "mortgage": "/money?section=mortgage",
    "credit_cards": "/money?section=credit_cards",
    "groceries": "/money?section=groceries",
    "borrower_profile": "/money?section=cash_on_hand",
    "research_rates": "/research-admin",
}


def _domain_path(domain: str, check: str, message: str) -> str:
    if check in CHECK_META:
        return CHECK_META[check]["fix_path"]
    if "due date" in message.lower() and domain == "bills":
        return "/money?section=bills"
    if "apr" in message.lower():
        return "/money?section=credit_cards"
    if "lender" in message.lower() or domain == "mortgage":
        return "/money?section=mortgage"
    return DOMAIN_PATHS.get(domain, "/money")


def build_data_fix_queue(session: Session) -> dict[str, Any]:
    report = assess_data_quality(session, emit_events=False)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for issue in report.get("blocking_issues", []) + report.get("warnings", []):
        check = issue.get("check", "unknown")
        domain = issue.get("domain", "")
        message = issue.get("message", "")
        key = f"{check}:{domain}:{issue.get('entity_id', '')}:{message[:40]}"
        if key in seen:
            continue
        seen.add(key)

        meta = CHECK_META.get(check, {})
        items.append(
            {
                "id": key,
                "check": check,
                "category": meta.get("label", check.replace("_", " ").title()),
                "severity": issue.get("severity", "warning"),
                "domain": domain,
                "message": message,
                "entity_id": issue.get("entity_id"),
                "fix_path": _domain_path(domain, check, message),
                "fix_hint": meta.get("fix_hint", issue.get("fix") or "Update this item"),
            }
        )

    # Sort blocking first
    items.sort(key=lambda i: (0 if i["severity"] == "blocking" else 1, i["domain"], i["category"]))

    return {
        "count": len(items),
        "blocking_count": sum(1 for i in items if i["severity"] == "blocking"),
        "warning_count": sum(1 for i in items if i["severity"] != "blocking"),
        "items": items,
        "overall_score": report.get("overall_score"),
        "status": report.get("status"),
    }
