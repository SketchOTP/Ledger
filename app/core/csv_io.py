"""CSV import/export for manual household data entry."""

from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any

from sqlmodel import Session, select

from app.api.schemas import BillCreate, DebtCreate
from app.core.budget_engine import sync_category_spending
from app.core.entry_validation import (
    validate_bill,
    validate_credit_card,
    validate_debt,
)
from app.db.models import Bill, CreditCard, Debt, Transaction

SUPPORTED_KINDS = ("bills", "debts", "credit_cards", "transactions")

EXPORT_HEADERS: dict[str, list[str]] = {
    "bills": ["name", "amount", "due_date", "recurrence", "required"],
    "debts": ["creditor", "balance", "apr", "minimum_payment", "due_date", "debt_type"],
    "credit_cards": [
        "issuer",
        "card_name",
        "balance",
        "credit_limit",
        "apr",
        "minimum_payment",
        "due_date",
    ],
    "transactions": ["amount", "category", "description", "transaction_date"],
}


def export_csv(session: Session, kind: str) -> str:
    if kind not in EXPORT_HEADERS:
        raise ValueError(f"Unsupported export kind: {kind}")

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_HEADERS[kind], extrasaction="ignore")
    writer.writeheader()

    if kind == "bills":
        for b in session.exec(select(Bill)).all():
            writer.writerow(
                {
                    "name": b.name,
                    "amount": b.amount,
                    "due_date": b.due_date.isoformat() if b.due_date else "",
                    "recurrence": b.recurrence or "monthly",
                    "required": b.required,
                }
            )
    elif kind == "debts":
        for d in session.exec(select(Debt)).all():
            writer.writerow(
                {
                    "creditor": d.creditor,
                    "balance": d.balance,
                    "apr": d.apr,
                    "minimum_payment": d.minimum_payment,
                    "due_date": d.due_date.isoformat() if d.due_date else "",
                    "debt_type": d.debt_type,
                }
            )
    elif kind == "credit_cards":
        for c in session.exec(select(CreditCard)).all():
            writer.writerow(
                {
                    "issuer": c.issuer,
                    "card_name": c.card_name,
                    "balance": c.balance,
                    "credit_limit": c.credit_limit,
                    "apr": c.apr,
                    "minimum_payment": c.minimum_payment,
                    "due_date": c.due_date.isoformat() if c.due_date else "",
                }
            )
    elif kind == "transactions":
        for t in session.exec(select(Transaction).order_by(Transaction.transaction_date.desc())).all():  # type: ignore[arg-type]
            writer.writerow(
                {
                    "amount": t.amount,
                    "category": t.category,
                    "description": t.description or "",
                    "transaction_date": t.transaction_date.isoformat(),
                }
            )

    return buf.getvalue()


def _parse_row_bool(val: str, default: bool = True) -> bool:
    if val is None or str(val).strip() == "":
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "y")


def import_csv(session: Session, kind: str, content: str) -> dict[str, Any]:
    if kind not in SUPPORTED_KINDS:
        raise ValueError(f"Unsupported import kind: {kind}")

    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        return {"created": 0, "errors": ["CSV is empty or missing header row"], "warnings": []}

    created = 0
    errors: list[str] = []
    warnings: list[str] = []

    for i, row in enumerate(reader, start=2):
        try:
            if kind == "bills":
                recurrence = (row.get("recurrence") or "monthly").strip().lower()
                payload = {
                    "name": row.get("name", "").strip(),
                    "amount": float(row.get("amount") or 0),
                    "due_date": row.get("due_date") or None,
                    "recurrence": recurrence,
                    "repeats": recurrence not in ("once", "none"),
                    "required": _parse_row_bool(row.get("required", "true")),
                }
                vr = validate_bill(payload)
                if not vr.ok:
                    errors.append(f"Row {i}: {'; '.join(vr.errors)}")
                    continue
                warnings.extend(f"Row {i}: {w}" for w in vr.warnings)
                from app.api.routes_bills import _bill_payload

                obj = Bill(**_bill_payload(BillCreate(**payload)))
                session.add(obj)

            elif kind == "debts":
                payload = {
                    "creditor": row.get("creditor", "").strip(),
                    "balance": float(row.get("balance") or 0),
                    "apr": float(row.get("apr") or 0),
                    "minimum_payment": float(row.get("minimum_payment") or 0),
                    "due_date": row.get("due_date") or None,
                    "debt_type": row.get("debt_type") or "credit_card",
                }
                vr = validate_debt(payload)
                if not vr.ok:
                    errors.append(f"Row {i}: {'; '.join(vr.errors)}")
                    continue
                obj = Debt(**DebtCreate(**payload).model_dump())
                session.add(obj)

            elif kind == "credit_cards":
                payload = {
                    "issuer": row.get("issuer", "").strip(),
                    "card_name": row.get("card_name", row.get("issuer", "")).strip(),
                    "balance": float(row.get("balance") or 0),
                    "credit_limit": float(row.get("credit_limit") or 0),
                    "apr": float(row.get("apr") or 0),
                    "minimum_payment": float(row.get("minimum_payment") or 0),
                    "due_date": row.get("due_date") or None,
                }
                vr = validate_credit_card(payload)
                if not vr.ok:
                    errors.append(f"Row {i}: {'; '.join(vr.errors)}")
                    continue
                warnings.extend(f"Row {i}: {w}" for w in vr.warnings)
                obj = CreditCard(**{k: v for k, v in payload.items() if hasattr(CreditCard, k)})
                if obj.credit_limit > 0:
                    obj.utilization_percent = round(obj.balance / obj.credit_limit * 100, 1)
                session.add(obj)

            elif kind == "transactions":
                amount = float(row.get("amount") or 0)
                category = (row.get("category") or "other").strip().lower() or "other"
                tx_date_raw = (row.get("transaction_date") or "").strip()
                if not tx_date_raw:
                    errors.append(f"Row {i}: transaction_date required")
                    continue
                if amount <= 0:
                    errors.append(f"Row {i}: amount must be positive")
                    continue
                obj = Transaction(
                    amount=amount,
                    category=category,
                    description=(row.get("description") or "").strip(),
                    transaction_date=date.fromisoformat(tx_date_raw[:10]),
                )
                session.add(obj)

            created += 1
        except Exception as exc:
            errors.append(f"Row {i}: {exc}")

    if created:
        session.commit()
        if kind == "transactions":
            sync_category_spending(session)

    result = {"created": created, "imported": created, "errors": errors, "warnings": warnings}
    return result
