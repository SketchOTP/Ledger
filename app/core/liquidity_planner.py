"""
Timeline-based liquidity planner — single checking account model.

Scheduled paychecks are included in planning simulations (waterfall) until marked
received; marking received only advances the schedule and removes that occurrence —
it does not change your checking balance (update that yourself when cash lands).
Bills and debt payments draw from checking in date order.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from sqlmodel import Session, select

from app.core.bill_schedule import _advance_months, bill_events_in_range
from app.core.budget_engine import planned_spending_daily_events, planned_spending_in_window
from app.core.cashflow_engine import (
    _effective_buffer,
    _following_pay_date,
    _missing_obligation_due_date_warnings,
    _next_pay_date,
    _resolve_checking_balance,
    safe_to_spend_detail,
)
from app.core.credit_card_schedule import cards_for_cashflow
from app.core.debt_schedule import debt_events_in_range, debts_for_cashflow
from app.core.housing_schedule import housing_events_in_range
from app.core.income_payment import expand_income_occurrences_unpaid, received_keys
from app.db.models import Bill, BudgetCategory, IncomeSource, MiscSpending, MortgageAccount

HORIZONS = (30, 60, 90, 180)
HORIZON_6MO = "6mo"
LIQUIDITY_HORIZONS: tuple[int | str, ...] = (*HORIZONS, HORIZON_6MO)
DEFAULT_TIMELINE_DAYS = 30
EventKind = Literal["income", "obligation"]


def _horizon_end(today: date, horizon: int | str) -> date:
    if horizon == HORIZON_6MO:
        return _advance_months(today, 6)
    return today + timedelta(days=int(horizon))


def _horizon_span_days(today: date, horizon: int | str) -> int:
    return (_horizon_end(today, horizon) - today).days


def max_liquidity_horizon_days(as_of: date | None = None) -> int:
    today = as_of or date.today()
    return max(_horizon_span_days(today, h) for h in LIQUIDITY_HORIZONS)


def _horizon_api_key(horizon: int | str) -> str:
    if horizon == HORIZON_6MO:
        return HORIZON_6MO
    return str(int(horizon))


def _dated_payment_plan(
    starting_cash: float,
    timeline: list[dict[str, Any]],
    living_schedule: list[dict[str, Any]],
    *,
    as_of: date,
    days: int = 30,
) -> dict[str, Any]:
    """One auditable checking-balance path, including dated usual spending.

    This is a projection only; income is assumed to post before payments on the
    same day, as in the canonical waterfall. No suggested saving or optional
    spending is silently entered as an actual transaction.
    """
    through = (as_of + timedelta(days=days)).isoformat()
    rows = [
        {
            "date": row["date"], "kind": row["kind"],
            "category": row["category"], "name": row["name"],
            "amount": round(float(row["amount"]), 2),
            "original_due_date": row.get("due_date"),
            "deferred_once": bool(row.get("deferred_once")),
            "grace_used": bool(row.get("grace_used")),
            "late_fee_date": row.get("late_fee_date"),
        }
        for row in timeline if row["date"] <= through
    ]
    rows.extend(
        {
            "date": row["date"], "kind": "obligation",
            "category": "living", "name": row.get("name") or "Usual everyday spending",
            "amount": round(float(row["amount"]), 2),
            "spending_kind": row.get("spending_kind", "daily"),
            "suggested_day": bool(row.get("suggested_day")),
            "awaiting_checking_update": bool(row.get("awaiting_checking_update")),
            "pending_checking_amount": round(float(row.get("pending_checking_amount", 0)), 2),
            "pending_grocery_amount": round(float(row.get("pending_grocery_amount", 0)), 2),
            "original_due_date": None, "deferred_once": False,
        }
        for row in living_schedule if row["date"] <= through and row["amount"] > 0
    )
    rows.sort(key=lambda row: (
        row["date"],
        0 if row["kind"] == "income" else 1 if row["category"] == "living" else 2,
        row["name"],
    ))
    balance = round(starting_cash, 2)
    lowest = balance
    for row in rows:
        balance = round(
            balance + row["amount"] if row["kind"] == "income"
            else balance - row["amount"], 2
        )
        row["projected_balance_after"] = balance
        lowest = min(lowest, balance)
    return {
        "as_of": as_of.isoformat(), "through": through,
        "opening_checking": round(starting_cash, 2),
        "projected_ending_checking": balance,
        "lowest_checking": round(lowest, 2),
        "rows": rows,
        "same_day_income_first": True,
        "suggestions_excluded_from_balance": True,
    }


def horizons_for_api(horizons: dict[int | str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Normalize horizon keys to JSON-safe strings (30..180 and 6mo)."""
    out: dict[str, dict[str, Any]] = {}
    for key, payload in horizons.items():
        row = dict(payload)
        row["horizon_key"] = key
        out[_horizon_api_key(key)] = row
    return out


def playbooks_for_api(playbooks: dict[int | str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    return {_horizon_api_key(key): rows for key, rows in playbooks.items()}


FLEXIBLE_TODAY_HORIZON_DAYS = max(HORIZONS)  # legacy default; prefer max_liquidity_horizon_days()
LIQUIDITY_UNDATED_WARNING = (
    "Some items are missing dates and were excluded from the liquidity timeline. "
    "Add dates to include them."
)


@dataclass
class _CashEvent:
    sort_key: tuple
    when: date
    kind: EventKind
    amount: float
    name: str
    category: str
    ref_id: str | None = None
    due_date: date | None = None
    grace_used: bool = False
    late_fee_date: date | None = None

    def __init__(
        self,
        when: date,
        kind: EventKind,
        amount: float,
        name: str,
        category: str,
        ref_id: str | None = None,
        due_date: date | None = None,
    ) -> None:
        self.when = when
        self.kind = kind
        self.amount = float(amount)
        self.name = name
        self.category = category
        self.ref_id = ref_id
        self.due_date = due_date
        self.grace_used = False
        self.late_fee_date = None
        # Income before obligations on the same calendar day (paycheck lands first).
        self.sort_key = (when, 0 if kind == "income" else 1, name)


def _iso_date(value: str | date | None) -> date | None:
    """Parse YYYY-MM-DD; return None when missing or invalid."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _obligation_when(scheduled: date, today: date) -> date:
    """Treat unpaid obligations due before today as due today in the simulation."""
    return today if scheduled < today else scheduled


def _obligation_key(ref_id: str | None, original_due: date) -> tuple[str, str]:
    return (ref_id or "", original_due.isoformat())


def _record_skipped_undated(
    skipped: list[dict[str, Any]],
    *,
    item_type: str,
    label: str,
    missing_field: str,
    ref_id: str | None = None,
) -> None:
    skipped.append(
        {
            "type": item_type,
            "label": label,
            "missing_field": missing_field,
            "reason": f"Missing {missing_field.replace('_', ' ')}; excluded from liquidity timeline.",
            "ref_id": ref_id,
        }
    )


def _liquidity_diagnostics(skipped: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "skipped_undated_events": skipped,
        "warning_count": len(skipped),
    }


def _append_obligation(
    events: list[_CashEvent],
    seen: set[tuple[str, str]],
    *,
    today: date,
    scheduled: date,
    amount: float,
    name: str,
    category: str,
    ref_id: str | None,
    original_due: date | None = None,
) -> None:
    due = original_due or scheduled
    key = _obligation_key(ref_id, due)
    if key in seen:
        return
    seen.add(key)
    when = _obligation_when(scheduled, today)
    events.append(
        _CashEvent(
            when,
            "obligation",
            amount,
            name,
            category,
            ref_id,
            due,
        )
    )


def _collect_events(
    session: Session, through: date, *, as_of: date | None = None
) -> tuple[list[_CashEvent], dict[str, Any]]:
    """Canonical literal-date event expansion used by all forward planners."""
    today = as_of or date.today()
    bills = list(session.exec(select(Bill)).all())
    debts = debts_for_cashflow(session)
    cards = cards_for_cashflow(session)
    misc = list(session.exec(select(MiscSpending)).all())

    events: list[_CashEvent] = []
    skipped_undated: list[dict[str, Any]] = []
    seen_obligations: set[tuple[str, str]] = set()
    for raw in bill_events_in_range(bills, today, through, session):
        when = _iso_date(raw.get("date"))
        if when is None:
            _record_skipped_undated(
                skipped_undated,
                item_type="bill",
                label=str(raw.get("name") or "Bill"),
                missing_field="due_date",
                ref_id=raw.get("bill_id"),
            )
            continue
        _append_obligation(
            events,
            seen_obligations,
            today=today,
            scheduled=when,
            amount=float(raw["amount"]),
            name=str(raw["name"]),
            category="bill",
            ref_id=raw.get("bill_id"),
            original_due=_iso_date(raw.get("due_date")) or when,
        )
    for raw in debt_events_in_range(debts, today, through, session):
        when = _iso_date(raw.get("date"))
        if when is None:
            _record_skipped_undated(
                skipped_undated,
                item_type="debt",
                label=str(raw.get("name") or "Debt"),
                missing_field="due_date",
                ref_id=raw.get("debt_id"),
            )
            continue
        _append_obligation(
            events,
            seen_obligations,
            today=today,
            scheduled=when,
            amount=float(raw["amount"]),
            name=str(raw["name"]),
            category="debt",
            ref_id=raw.get("debt_id"),
            original_due=_iso_date(raw.get("due_date")) or when,
        )
    for raw in housing_events_in_range(session, today, through):
        when = _iso_date(raw.get("date"))
        if when is None:
            _record_skipped_undated(
                skipped_undated,
                item_type="housing",
                label=str(raw.get("name") or "Housing"),
                missing_field="due_date",
                ref_id=raw.get("mortgage_id") or raw.get("profile_id"),
            )
            continue
        _append_obligation(
            events,
            seen_obligations,
            today=today,
            scheduled=when,
            amount=float(raw["amount"]),
            name=str(raw["name"]),
            category=str(raw.get("type", "mortgage")),
            ref_id=raw.get("mortgage_id") or raw.get("profile_id"),
            original_due=_iso_date(raw.get("due_date")) or when,
        )
    from app.core.credit_card_payment import credit_card_events_in_range_unpaid
    from app.core.credit_card_payment import paid_keys as card_paid_keys
    from app.core.misc_payment import misc_events_in_range_unpaid
    from app.core.misc_payment import paid_keys as misc_paid_keys

    for raw in credit_card_events_in_range_unpaid(cards, today, through, card_paid_keys(session), session):
        when = _iso_date(raw.get("date"))
        if when is None:
            _record_skipped_undated(
                skipped_undated,
                item_type="credit_card",
                label=str(raw.get("name") or "Credit card"),
                missing_field="due_date",
                ref_id=raw.get("credit_card_id"),
            )
            continue
        _append_obligation(
            events,
            seen_obligations,
            today=today,
            scheduled=when,
            amount=float(raw["amount"]),
            name=str(raw["name"]),
            category="credit_card",
            ref_id=raw.get("credit_card_id"),
            original_due=_iso_date(raw.get("due_date")) or when,
        )
    for raw in misc_events_in_range_unpaid(misc, today, through, misc_paid_keys(session)):
        when = _iso_date(raw.get("date"))
        if when is None:
            _record_skipped_undated(
                skipped_undated,
                item_type="misc",
                label=str(raw.get("name") or "Misc"),
                missing_field="due_date",
                ref_id=raw.get("misc_spending_id"),
            )
            continue
        _append_obligation(
            events,
            seen_obligations,
            today=today,
            scheduled=when,
            amount=float(raw["amount"]),
            name=str(raw["name"]),
            category="misc",
            ref_id=raw.get("misc_spending_id"),
            original_due=_iso_date(raw.get("due_date")) or when,
        )

    from app.core.unpaid_obligations import list_all_unpaid_past_due

    seen_past_due: set[tuple[str, str]] = set()
    for item in list_all_unpaid_past_due(session, as_of=today):
        ref = (
            item.get("bill_id")
            or item.get("debt_id")
            or item.get("mortgage_id")
            or item.get("credit_card_id")
            or item.get("misc_spending_id")
            or item.get("profile_id")
            or ""
        )
        original_due = _iso_date(item.get("due_date"))
        if original_due is None:
            _record_skipped_undated(
                skipped_undated,
                item_type=str(item.get("kind") or "obligation"),
                label=str(item.get("name") or "Past due"),
                missing_field="due_date",
                ref_id=ref or None,
            )
            continue
        key = _obligation_key(ref or None, original_due)
        if key in seen_obligations or key in seen_past_due:
            continue
        seen_past_due.add(key)
        seen_obligations.add(key)
        kind = item.get("kind")
        if kind == "debt":
            cat = "debt"
        elif kind == "mortgage":
            cat = "mortgage"
        elif kind == "credit_card":
            cat = "credit_card"
        elif kind == "misc":
            cat = "misc"
        elif kind == "rent":
            cat = "rent"
        else:
            cat = "bill"
        events.append(
            _CashEvent(
                today,
                "obligation",
                item["amount"],
                item["name"],
                cat,
                ref or None,
                original_due,
            )
        )

    sources = list(session.exec(select(IncomeSource)).all())
    income_keys = received_keys(session)
    for src in sources:
        if not src.next_pay_date:
            _record_skipped_undated(
                skipped_undated,
                item_type="income",
                label=src.source_name or "Paycheck",
                missing_field="next_pay_date",
                ref_id=src.id,
            )
            continue
        for pay_date, amount in expand_income_occurrences_unpaid(
            src, today, through, income_keys
        ):
            if pay_date is None:
                _record_skipped_undated(
                    skipped_undated,
                    item_type="income",
                    label=src.source_name or "Paycheck",
                    missing_field="next_pay_date",
                    ref_id=src.id,
                )
                continue
            events.append(
                _CashEvent(
                    pay_date,
                    "income",
                    amount,
                    src.source_name or "Paycheck",
                    "income",
                    src.id,
                    pay_date,
                )
            )

    events.sort(key=lambda e: e.sort_key)
    diagnostics = _liquidity_diagnostics(skipped_undated)
    if skipped_undated:
        logging.getLogger(__name__).warning(
            "Liquidity planner skipped undated events: %s",
            ", ".join(f'{e["type"]} "{e["label"]}"' for e in skipped_undated),
        )
    return events, diagnostics


def _simulate_waterfall(
    starting_cash: float,
    events: list[_CashEvent],
    *,
    through: date | None = None,
) -> tuple[float, list[dict[str, Any]], float | None]:
    """Walk events in date order against one checking balance."""
    checking = starting_cash
    steps: list[dict[str, Any]] = []
    first_shortfall: float | None = None

    for ev in events:
        if through and ev.when > through:
            break
        if ev.kind == "income":
            checking = round(checking + ev.amount, 2)
            steps.append(
                {
                    "date": ev.when.isoformat(),
                    "action": "deposit_to_checking",
                    "name": ev.name,
                    "amount": ev.amount,
                    "category": "income",
                    "pay_date": ev.when.isoformat(),
                    "due_date": ev.when.isoformat(),
                    "income_source_id": ev.ref_id,
                    "planned": True,
                    "can_mark_received": True,
                    "checking_balance": checking,
                    "balance_after": checking,
                    "detail": (
                        f"${ev.amount:,.0f} planned into checking ({ev.name}) — "
                        "mark received when deposited; update checking yourself"
                    ),
                }
            )
            continue

        due = ev.amount
        if checking >= due:
            checking = round(checking - due, 2)
            is_bill = ev.category == "bill"
            is_debt = ev.category == "debt"
            is_mortgage = ev.category == "mortgage"
            is_misc = ev.category == "misc"
            is_card = ev.category == "credit_card"
            is_rent = ev.category == "rent"
            steps.append(
                {
                    "date": ev.when.isoformat(),
                    "action": "pay_from_checking",
                    "name": ev.name,
                    "amount": due,
                    "category": ev.category,
                    "due_date": (ev.due_date or ev.when).isoformat(),
                    "bill_id": ev.ref_id if is_bill else None,
                    "debt_id": ev.ref_id if is_debt else None,
                    "mortgage_id": ev.ref_id if is_mortgage else None,
                    "misc_spending_id": ev.ref_id if is_misc else None,
                    "credit_card_id": ev.ref_id if is_card else None,
                    "profile_id": ev.ref_id if is_rent else None,
                    "can_mark_paid": (
                        is_bill
                        or is_debt
                        or is_mortgage
                        or is_misc
                        or is_card
                        or is_rent
                    ),
                    "checking_balance": checking,
                    "balance_after": checking,
                    "detail": (
                        f"Pay ${due:,.0f} for {ev.name} from checking · "
                        f"balance ${checking:,.0f}"
                    ),
                }
            )
        else:
            gap = round(due - checking, 2)
            if first_shortfall is None:
                first_shortfall = gap
            is_bill = ev.category == "bill"
            is_debt = ev.category == "debt"
            is_mortgage = ev.category == "mortgage"
            is_misc = ev.category == "misc"
            is_card = ev.category == "credit_card"
            is_rent = ev.category == "rent"
            steps.append(
                {
                    "date": ev.when.isoformat(),
                    "action": "shortfall",
                    "name": ev.name,
                    "amount": due,
                    "category": ev.category,
                    "due_date": (ev.due_date or ev.when).isoformat(),
                    "bill_id": ev.ref_id if is_bill else None,
                    "debt_id": ev.ref_id if is_debt else None,
                    "mortgage_id": ev.ref_id if is_mortgage else None,
                    "misc_spending_id": ev.ref_id if is_misc else None,
                    "credit_card_id": ev.ref_id if is_card else None,
                    "profile_id": ev.ref_id if is_rent else None,
                    "can_mark_paid": bool(ev.ref_id),
                    "shortfall": gap,
                    "checking_balance": 0.0,
                    "balance_after": 0.0,
                    "detail": (
                        f"Need ${due:,.0f} for {ev.name} on {ev.when.isoformat()} "
                        f"but checking only has ${checking:,.0f} — ${gap:,.0f} short"
                    ),
                }
            )
            checking = 0.0

    return checking, steps, first_shortfall


def _waterfall_lowest_checking(playbook: list[dict[str, Any]], starting_cash: float) -> float:
    """Lowest checking balance reached during a waterfall (includes opening balance)."""
    lowest = float(starting_cash)
    for step in playbook:
        bal = step.get("checking_balance")
        if bal is not None:
            lowest = min(lowest, float(bal))
    return round(lowest, 2)


def _safe_to_spend_from_waterfall(
    playbook: list[dict[str, Any]],
    *,
    starting_cash: float,
    buffer: float,
) -> tuple[float, bool, float]:
    """How much you can spend today without breaking this waterfall plan."""
    has_shortfall = any(s.get("action") == "shortfall" for s in playbook)
    lowest = _waterfall_lowest_checking(playbook, starting_cash)
    if has_shortfall or lowest < buffer:
        return 0.0, has_shortfall, lowest
    return max(0.0, round(lowest - buffer, 2)), has_shortfall, lowest


def _playbook_step_kind(action: str | None) -> str:
    if action == "deposit_to_checking":
        return "income"
    if action == "pay_from_checking":
        return "obligation"
    if action == "shortfall":
        return "shortfall"
    return "other"


def _build_flexible_today_trace(
    *,
    starting_cash: float,
    buffer: float,
    playbook: list[dict[str, Any]],
    flexible_today: float,
    has_shortfall: bool,
    lowest_balance: float,
    ending_balance: float,
    horizon_days: int,
    horizon_end: date,
    first_shortfall: dict[str, Any] | None,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Auditable waterfall for Home — opening balance through each event to Flexible Today."""
    today = as_of or date.today()
    events: list[dict[str, Any]] = [
        {
            "step": 0,
            "kind": "opening",
            "date": today.isoformat(),
            "name": "Opening checking balance",
            "amount": None,
            "balance_after": round(starting_cash, 2),
        }
    ]
    for idx, step in enumerate(playbook, start=1):
        action = step.get("action")
        events.append(
            {
                "step": idx,
                "kind": _playbook_step_kind(action),
                "date": (step.get("date") or "")[:10],
                "name": step.get("name"),
                "amount": step.get("amount"),
                "category": step.get("category"),
                "balance_after": step.get("checking_balance"),
                "shortfall": step.get("shortfall"),
                "detail": step.get("detail"),
            }
        )

    if has_shortfall and first_shortfall:
        gap = float(first_shortfall.get("shortfall") or 0)
        summary = (
            f"Forward plan through {horizon_end.isoformat()} fails — ${gap:,.0f} short for "
            f"{first_shortfall.get('name')} on {(first_shortfall.get('date') or '')[:10]} "
            "even with all planned income. Flexible Today is $0."
        )
    elif lowest_balance < buffer:
        summary = (
            f"Lowest projected checking is ${lowest_balance:,.0f} before your ${buffer:,.0f} "
            f"buffer through {horizon_end.isoformat()}. Flexible Today is $0."
        )
    else:
        summary = (
            f"Maximum spend today: ${flexible_today:,.0f} — lowest projected checking "
            f"${lowest_balance:,.0f} through {horizon_end.isoformat()} "
            f"(buffer ${buffer:,.0f}, ending ${ending_balance:,.0f})."
        )

    return {
        "starting_balance": round(starting_cash, 2),
        "buffer": round(buffer, 2),
        "lowest_balance": round(lowest_balance, 2),
        "ending_balance": round(ending_balance, 2),
        "flexible_today": round(flexible_today, 2),
        "has_shortfall": has_shortfall,
        "horizon_days": horizon_days,
        "horizon_end": horizon_end.isoformat(),
        "same_day_order": "income_before_obligations",
        "past_due_rule": "unpaid_obligations_due_immediately",
        "income_rule": "scheduled_only_if_not_marked_received",
        "events": events,
        "summary": summary,
        "first_shortfall_date": (
            (first_shortfall.get("date") or "")[:10] if first_shortfall else None
        ),
        "first_shortfall_name": first_shortfall.get("name") if first_shortfall else None,
    }


def compute_flexible_today(
    *,
    starting_cash: float,
    buffer: float,
    events: list[_CashEvent],
    horizon_days: int | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    if horizon_days is None:
        horizon_days = max_liquidity_horizon_days(as_of)
    """
    Maximum you can spend right now while surviving all known scheduled obligations.

    Model: spend S today at t=0 reduces every future balance by S. The largest valid S
    keeps the simulated checking balance >= buffer for every event through ``horizon_days``.
    Equivalent to max(0, lowest_balance_without_spending - buffer) when the base plan
    has no shortfall; otherwise $0.
    """
    today = as_of or date.today()
    horizon_end = today + timedelta(days=horizon_days)
    ending, playbook, _ = _simulate_waterfall(starting_cash, events, through=horizon_end)
    has_shortfall = any(s.get("action") == "shortfall" for s in playbook)
    lowest = _waterfall_lowest_checking(playbook, starting_cash)
    first_shortfall = next((s for s in playbook if s.get("action") == "shortfall"), None)

    if has_shortfall or lowest < buffer:
        flexible = 0.0
    else:
        flexible = max(0.0, round(lowest - buffer, 2))

    trace = _build_flexible_today_trace(
        starting_cash=starting_cash,
        buffer=buffer,
        playbook=playbook,
        flexible_today=flexible,
        has_shortfall=has_shortfall,
        lowest_balance=lowest,
        ending_balance=ending,
        horizon_days=horizon_days,
        horizon_end=horizon_end,
        first_shortfall=first_shortfall,
        as_of=today,
    )

    return {
        "flexible_today": flexible,
        "has_shortfall": has_shortfall,
        "lowest_balance": lowest,
        "ending_balance": round(ending, 2),
        "buffer": round(buffer, 2),
        "horizon_days": horizon_days,
        "horizon_end": horizon_end.isoformat(),
        "method": "max_spend_forward_waterfall",
        "formula": (
            "Opening checking minus the largest spend today such that every planned "
            "income (when it lands) and obligation (due-date order; income before "
            "obligations same day) through the horizon keeps balance >= buffer."
        ),
        "trace": trace,
    }


def _build_pay_cycle_outlook(
    *,
    cash: float,
    buffer: float,
    playbook_cycle: list[dict[str, Any]],
    first_cycle_end: date,
    cycle_cash: float,
    next_pay: date | None,
    following_pay: date | None,
) -> dict[str, Any]:
    """
    Pay-cycle CFO view: walk today's checking through every obligation and paycheck
    until the day before the following paycheck.
    """
    safe, has_shortfall, lowest = _safe_to_spend_from_waterfall(
        playbook_cycle,
        starting_cash=cash,
        buffer=buffer,
    )
    first_shortfall = next((s for s in playbook_cycle if s.get("action") == "shortfall"), None)
    pays = [s for s in playbook_cycle if s.get("action") == "pay_from_checking"]
    deposits = [s for s in playbook_cycle if s.get("action") == "deposit_to_checking"]
    obligations_total = round(sum(float(p.get("amount") or 0) for p in pays), 2)
    income_total = round(sum(float(d.get("amount") or 0) for d in deposits), 2)

    if has_shortfall and first_shortfall:
        gap = float(first_shortfall.get("shortfall") or 0)
        summary = (
            f"Not OK through {first_cycle_end.isoformat()} — ${gap:,.0f} short for "
            f"{first_shortfall.get('name')} on {(first_shortfall.get('date') or '')[:10]} "
            "even with planned paychecks."
        )
        status = "shortfall"
    elif lowest < buffer:
        status = "tight"
        summary = (
            f"Tight through {first_cycle_end.isoformat()} — lowest projected checking is "
            f"${lowest:,.0f} (below your ${buffer:,.0f} buffer)."
        )
    else:
        status = "ok"
        summary = (
            f"Through {first_cycle_end.isoformat()}: ${income_total:,.0f} planned income vs "
            f"${obligations_total:,.0f} in obligations — about ${safe:,.0f} safe to spend now."
        )

    return {
        "status": status,
        "summary": summary,
        "safe_to_spend": safe,
        "has_shortfall": has_shortfall,
        "pay_cycle_end": first_cycle_end.isoformat(),
        "next_pay_date": next_pay.isoformat() if next_pay else None,
        "following_pay_date": following_pay.isoformat() if following_pay else None,
        "scheduled_income": income_total,
        "scheduled_obligations": obligations_total,
        "lowest_balance": lowest,
        "ending_balance": round(cycle_cash, 2),
        "first_shortfall_date": (
            (first_shortfall.get("date") or "")[:10] if first_shortfall else None
        ),
        "first_shortfall_name": first_shortfall.get("name") if first_shortfall else None,
    }


def _daily_balances(
    starting_cash: float,
    events: list[_CashEvent],
    horizon_days: int,
    *,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    today = as_of or date.today()
    end = today + timedelta(days=horizon_days)
    by_day: dict[str, tuple[float, float]] = {}
    for ev in events:
        if ev.when > end:
            continue
        ds = ev.when.isoformat()
        inc, out = by_day.get(ds, (0.0, 0.0))
        if ev.kind == "income":
            by_day[ds] = (inc + ev.amount, out)
        else:
            by_day[ds] = (inc, out + ev.amount)

    series: list[dict[str, Any]] = []
    balance = starting_cash
    lowest = balance
    lowest_date = today.isoformat()
    for offset in range(horizon_days + 1):
        d = today + timedelta(days=offset)
        ds = d.isoformat()
        inc, out = by_day.get(ds, (0.0, 0.0))
        balance = round(balance + inc - out, 2)
        if balance < lowest:
            lowest = balance
            lowest_date = ds
        series.append(
            {
                "date": ds,
                "income": round(inc, 2),
                "obligations": round(out, 2),
                "balance": balance,
            }
        )
    return series, {"date": lowest_date, "balance": round(lowest, 2)}


def _horizon_summary(
    starting_cash: float,
    events: list[_CashEvent],
    buffer: float,
    horizon_days: int,
    daily_series: list[dict[str, Any]],
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    today = as_of or date.today()
    end = today + timedelta(days=horizon_days)
    income = sum(e.amount for e in events if e.kind == "income" and e.when <= end)
    obligations = sum(e.amount for e in events if e.kind == "obligation" and e.when <= end)
    window = daily_series[: horizon_days + 1]
    end_balance = window[-1]["balance"] if window else starting_cash
    min_balance = min((p["balance"] for p in window), default=starting_cash)

    # Money you can spend without dipping below buffer during this window.
    flexible = max(0.0, round(min_balance - buffer, 2))
    # Money left at horizon end after keeping your buffer target.
    room_at_end = max(0.0, round(end_balance - buffer, 2))
    # If scheduled pays all land, this is what remains at the horizon date.
    spend_or_save = room_at_end

    return {
        "days": horizon_days,
        "income_expected": round(income, 2),
        "obligations_scheduled": round(obligations, 2),
        "net_scheduled": round(income - obligations, 2),
        "projected_balance": round(end_balance, 2),
        "lowest_balance": round(min_balance, 2),
        "flexible_without_hitting_buffer": flexible,
        "room_at_end_after_buffer": room_at_end,
        "spend_or_save_budget": spend_or_save,
    }


def _build_forward_outlook(
    *,
    cash: float,
    buffer: float,
    playbook_30: list[dict[str, Any]],
    horizon_30: dict[str, Any],
    ending_balance_30: float,
) -> dict[str, Any]:
    """
    30-day forward plan: all scheduled bills/debts/cards/mortgage/misc spending vs planned paychecks.

    safe_to_spend = how much you can spend today without breaking that plan (lowest projected
    checking through 30 days, minus buffer), or $0 if any obligation cannot be covered on schedule.
    """
    has_shortfall = any(s["action"] == "shortfall" for s in playbook_30)
    first_shortfall = next((s for s in playbook_30 if s["action"] == "shortfall"), None)

    income = float(horizon_30.get("income_expected") or 0)
    obligations = float(horizon_30.get("obligations_scheduled") or 0)
    net = float(horizon_30.get("net_scheduled") or 0)
    lowest_raw = horizon_30.get("lowest_balance")
    lowest = float(cash if lowest_raw is None else lowest_raw)
    projected = float(ending_balance_30)

    if has_shortfall and first_shortfall:
        gap = float(first_shortfall.get("shortfall") or 0)
        safe = 0.0
        status = "shortfall"
        summary = (
            f"Not OK in the next 30 days — ${gap:,.0f} short for {first_shortfall.get('name')} "
            f"on {(first_shortfall.get('date') or '')[:10]} even with planned paychecks."
        )
    elif projected < 0:
        safe = 0.0
        status = "shortfall"
        summary = (
            f"Not OK in the next 30 days — scheduled bills exceed checking and planned income "
            f"by ${abs(projected):,.0f}."
        )
    elif lowest < buffer:
        safe = 0.0
        status = "tight"
        summary = (
            f"Tight for 30 days — lowest projected checking is ${lowest:,.0f} "
            f"(below your ${buffer:,.0f} buffer)."
        )
    elif projected < buffer * 2:
        safe = max(0.0, round(lowest - buffer, 2))
        status = "tight"
        summary = (
            f"Tight but workable — ${income:,.0f} planned income vs ${obligations:,.0f} in bills; "
            f"about ${projected:,.0f} left after 30 days."
        )
    else:
        safe = max(0.0, round(lowest - buffer, 2))
        status = "ok"
        summary = (
            f"On track for 30 days — ${income:,.0f} planned income covers ${obligations:,.0f} "
            f"in scheduled bills (net ${net:+,.0f})."
        )

    return {
        "status": status,
        "summary": summary,
        "safe_to_spend": round(safe, 2),
        "has_shortfall": has_shortfall,
        "scheduled_income": round(income, 2),
        "scheduled_obligations": round(obligations, 2),
        "net_scheduled": round(net, 2),
        "projected_balance": round(projected, 2),
        "lowest_balance": round(lowest, 2),
        "first_shortfall_date": (
            (first_shortfall.get("date") or "")[:10] if first_shortfall else None
        ),
        "first_shortfall_name": first_shortfall.get("name") if first_shortfall else None,
    }


def _build_recommendations(
    playbook: list[dict[str, Any]],
    horizons: dict[int, dict[str, Any]],
    *,
    starting_cash: float,
    buffer: float,
    next_pay: date | None,
    following_pay: date | None,
    first_cycle_end: date | None,
) -> list[str]:
    recs: list[str] = []
    pays = [s for s in playbook if s["action"] == "pay_from_checking"]
    shortfalls = [s for s in playbook if s["action"] == "shortfall"]
    deposits = [s for s in playbook if s["action"] == "deposit_to_checking"]

    recs.append(
        f"Forward plan from ${starting_cash:,.0f} in checking today — all bills, debts, cards, "
        "mortgage and misc spending vs every planned paycheck in date order."
    )

    if deposits:
        first_dep = deposits[0]
        pays_before = [p for p in pays if p["date"] < first_dep["date"]]
        pays_after = [p for p in pays if p["date"] >= first_dep["date"]]
        if pays_before:
            names = ", ".join(f"{p['name']} (${p['amount']:,.0f})" for p in pays_before[:4])
            recs.append(
                f"From today's checking, cover {names} before the "
                f"{first_dep['date']} deposit (+${first_dep['amount']:,.0f})."
            )
        if pays_after:
            nxt = pays_after[0]
            recs.append(
                f"After ${first_dep['amount']:,.0f} hits checking on {first_dep['date']}, "
                f"plan for {nxt['name']} (${nxt['amount']:,.0f} on {nxt['date']}) — "
                "same account, just more balance on deposit day."
            )
    elif pays:
        names = ", ".join(f"{p['name']} (${p['amount']:,.0f})" for p in pays[:4])
        recs.append(f"Upcoming from checking: {names}.")

    if shortfalls:
        s = shortfalls[0]
        recs.append(
            f"Gap alert: {s['detail']} — delay extras, move a due date, or add income before that date."
        )
    elif first_cycle_end:
        last = next(
            (s for s in reversed(playbook) if s["action"] in ("pay_from_checking", "deposit_to_checking")),
            None,
        )
        if last and last.get("checking_balance") is not None:
            bal = float(last["checking_balance"])
            if buffer > 0:
                recs.append(
                    f"Through {first_cycle_end.isoformat()}, scheduled in/out leaves about "
                    f"${bal:,.0f} in checking (${max(0, bal - buffer):,.0f} above your ${buffer:,.0f} buffer)."
                )
            else:
                recs.append(
                    f"Through {first_cycle_end.isoformat()}, scheduled in/out leaves about "
                    f"${bal:,.0f} in checking after essentials."
                )

    h30 = horizons.get(30, {})
    if h30.get("spend_or_save_budget", 0) > 0:
        recs.append(
            f"Next 30 days: ~${h30['income_expected']:,.0f} deposits into checking vs "
            f"~${h30['obligations_scheduled']:,.0f} scheduled out — "
            f"about ${h30['spend_or_save_budget']:,.0f} room to spend or save if you stick to essentials."
        )
    elif buffer > 0 and h30.get("lowest_balance", 0) < buffer:
        recs.append(
            "Checking dips below your buffer in the next 30 days — go easy on extras until after the next deposit."
        )

    if next_pay and following_pay:
        recs.append(
            f"Next deposit into checking: {next_pay.isoformat()}, then {following_pay.isoformat()}."
        )

    return recs


def _apply_mortgage_grace_when_needed(
    session: Session, events: list[_CashEvent], living: list[dict[str, Any]],
    cash: float, as_of: date,
) -> None:
    """Use recorded mortgage grace only to prevent a dated essentials shortfall.

    This changes a forecast payment date, never the contractual due date or an
    actual payment. An existing explicit one-time deferral takes precedence.
    """
    from app.core.cash_rescue import _lowest_before

    mortgages = {row.id: row for row in session.exec(select(MortgageAccount)).all()}
    for event in sorted(events, key=lambda row: row.sort_key):
        mortgage = mortgages.get(event.ref_id) if event.category == "mortgage" else None
        if mortgage is None or mortgage.late_by_date is None or event.due_date is None:
            continue
        due_anchor = mortgage.due_by_date or mortgage.start_date
        if due_anchor is None or event.when != max(as_of, event.due_date):
            continue
        grace_days = (mortgage.late_by_date - due_anchor).days
        if not 0 < grace_days < 28:
            continue
        deadline = event.due_date + timedelta(days=grace_days)
        if deadline < as_of:
            continue
        rows = [{"date": row.when.isoformat(), "kind": row.kind, "amount": row.amount,
                 "name": row.name} for row in events] + living
        baseline = _lowest_before(cash, rows, deadline)
        if baseline >= 0:
            continue
        incomes = sorted({row.when for row in events if row.kind == "income"
                          and event.when < row.when <= deadline})
        for income_day in incomes:
            proposal = [{"date": (income_day if row is event else row.when).isoformat(),
                         "kind": row.kind, "amount": row.amount, "name": row.name}
                        for row in events] + living
            if _lowest_before(cash, proposal, deadline) >= 0:
                event.when = income_day
                event.sort_key = (income_day, 1, event.name)
                event.grace_used = True
                event.late_fee_date = deadline
                break
    events.sort(key=lambda row: row.sort_key)


def build_liquidity_plan(
    session: Session,
    starting_cash: float | None = None,
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Build the canonical rolling, literal-date financial timeline."""
    today = as_of or date.today()
    max_horizon_days = max_liquidity_horizon_days(today)
    through = today + timedelta(days=max_horizon_days)
    cash = _resolve_checking_balance(session, starting_cash)
    buffer = _effective_buffer(session)
    sources = list(session.exec(select(IncomeSource)).all())
    next_pay, next_pay_amount = _next_pay_date(sources, today)
    following_pay = _following_pay_date(sources, next_pay) if next_pay else today + timedelta(days=28)
    first_cycle_end = following_pay - timedelta(days=1)

    events, diagnostics = _collect_events(session, through, as_of=today)
    planning_end = _advance_months(today, 6)
    planning_days = (planning_end - today).days
    planned_living_schedule = planned_spending_daily_events(session, today, planning_end)
    _apply_mortgage_grace_when_needed(session, events, planned_living_schedule, cash, today)
    playbooks: dict[int | str, list[dict[str, Any]]] = {}
    horizon_endings: dict[int | str, float] = {}
    for horizon in LIQUIDITY_HORIZONS:
        end = _horizon_end(today, horizon)
        ending, playbooks[horizon], _ = _simulate_waterfall(cash, events, through=end)
        horizon_endings[horizon] = ending
    _, playbook_cycle, cycle_shortfall = _simulate_waterfall(
        cash, events, through=first_cycle_end
    )
    daily_series, lowest = _daily_balances(cash, events, max_horizon_days, as_of=today)

    horizons: dict[int | str, dict[str, Any]] = {}
    for horizon in LIQUIDITY_HORIZONS:
        span = _horizon_span_days(today, horizon)
        horizons[horizon] = _horizon_summary(
            cash, events, buffer, span, daily_series, as_of=today
        )
        horizons[horizon]["horizon_key"] = horizon
        horizons[horizon]["days"] = span
        # The waterfall stops spending at $0 when a payment cannot be made;
        # its ending balance therefore omits the unpaid remainder. The signed
        # daily series retains that liability and is the truthful projection.
        horizons[horizon]["waterfall_funded_balance"] = round(horizon_endings[horizon], 2)
        flex = compute_flexible_today(
            starting_cash=cash,
            buffer=buffer,
            events=events,
            horizon_days=span,
            as_of=today,
        )
        horizons[horizon]["flexible_today"] = flex["flexible_today"]
        if horizon in (180, HORIZON_6MO):
            horizons[horizon]["flexible_today_trace"] = flex

    cycle_signed_cash = round(
        cash + sum(
            event.amount if event.kind == "income" else -event.amount
            for event in events if event.when <= first_cycle_end
        ), 2,
    )
    spendable_after_essentials = (
        0.0 if cycle_shortfall is not None
        else max(0.0, round(cycle_signed_cash - buffer, 2))
    )
    playbook_30 = playbooks.get(DEFAULT_TIMELINE_DAYS, [])
    horizon_30 = horizons.get(30, {})
    ending_balance_30 = float(horizon_30["projected_balance"])
    pay_cycle_outlook = _build_pay_cycle_outlook(
        cash=cash,
        buffer=buffer,
        playbook_cycle=playbook_cycle,
        first_cycle_end=first_cycle_end,
        cycle_cash=cycle_signed_cash,
        next_pay=next_pay,
        following_pay=following_pay,
    )
    flexible_result = compute_flexible_today(
        starting_cash=cash,
        buffer=buffer,
        events=events,
        horizon_days=FLEXIBLE_TODAY_HORIZON_DAYS,
        as_of=today,
    )
    flexible_today = float(flexible_result["flexible_today"])
    forward = _build_forward_outlook(
        cash=cash,
        buffer=buffer,
        playbook_30=playbook_30,
        horizon_30=horizon_30,
        ending_balance_30=ending_balance_30,
    )
    safe_to_spend_now = float(forward["safe_to_spend"])
    has_shortfall = bool(flexible_result["has_shortfall"])

    planned_variable = planned_spending_in_window(session, today, today + timedelta(days=30))
    from app.core.money_state import build_money_state

    money_state = build_money_state(
        session,
        as_of=today,
        starting_cash=cash,
        buffer_target=buffer,
        horizons=horizons,
        daily_series=daily_series,
        planned_variable_spending=planned_variable,
        planned_spending_schedule=planned_living_schedule,
    )

    from app.core.bill_deferral import bill_deferrals
    from app.core.required_payment_deferral import payment_deferrals

    approved_bill_dates = bill_deferrals(session)
    approved_payment_dates = payment_deferrals(session)
    canonical_timeline = [
        {
            "date": event.when.isoformat(),
            "kind": event.kind,
            "amount": event.amount,
            "name": event.name,
            "category": event.category,
            "ref_id": event.ref_id,
            "due_date": (event.due_date or event.when).isoformat(),
            "grace_used": event.grace_used,
            "late_fee_date": event.late_fee_date.isoformat() if event.late_fee_date else None,
            "deferred_once": (
                (event.category == "bill" and (event.ref_id, event.due_date) in approved_bill_dates)
                or (event.category in {"debt", "mortgage", "credit_card"} and
                    (event.category, event.ref_id, event.due_date) in approved_payment_dates)
            ),
        }
        for event in events
    ]
    money_state["payment_plan_30_days"] = _dated_payment_plan(
        cash, canonical_timeline, planned_living_schedule, as_of=today
    )
    money_state["payment_plan"] = _dated_payment_plan(
        cash, canonical_timeline, planned_living_schedule, as_of=today, days=planning_days
    )
    from app.core.pay_period_allowance import build_pay_period_allowance
    allowance = build_pay_period_allowance(session, as_of=today, through=planning_end,
                                          opening_cash=cash, payment_rows=money_state["payment_plan"]["rows"])
    if allowance is not None:
        money_state["allowance"] = allowance
        for key in ("free_to_use_now", "total_flexible_30_days", "discretionary_spending_30_days"):
            money_state[key] = allowance["remaining"]
        money_state["cash_cushion_refill"] = next((row["amount"] for row in allowance["contributions"]
                                                  if row["date"] <= today.isoformat()), 0)
        money_state["working_safety_holdback"] = allowance["emergency_current"] or 0
        money_state["deployable_surplus"] = min(money_state["deployable_surplus"], allowance["remaining"])
    from app.core.cash_rescue import build_cash_rescue_plan

    money_state["cash_rescue"] = build_cash_rescue_plan(
        cash=cash,
        events=canonical_timeline,
        as_of=today,
        normal_spending_reserve=float(money_state["expected_variable_reserve"]),
        normal_spending_schedule=planned_living_schedule,
        normal_spending_entered=any(
            float(category.monthly_limit) > 0
            for category in session.exec(select(BudgetCategory)).all()
        ),
        cushion_target=float((allowance["emergency_current"] or 0) if allowance else money_state["cash_cushion_goal"]),
        cushion_configured=float(money_state["cash_cushion_goal"]) > 0,
        horizon_days=planning_days,
    )

    horizon_30 = horizons.get(30, {})
    missing_due_dates = list((diagnostics or {}).get("skipped_undated_events") or [])
    missing_due_dates.extend(
        {
            "type": "required_obligation",
            "label": "required payment",
            "missing_field": "due_date",
        }
        for _warning in _missing_obligation_due_date_warnings(session)
    )
    first_planning_gap = money_state["cash_rescue"].get("first_gap") or {}
    first_shortfall_date = first_planning_gap.get("date")
    first_shortfall_name = first_planning_gap.get("payment")
    if missing_due_dates:
        coverage_status = "unknown"
        coverage_message = (
            "I need a due date for some required payments before I can confirm everything is covered."
        )
    elif first_planning_gap:
        coverage_status = "shortfall"
        shortfall = first_planning_gap.get("amount", 0)
        target = first_shortfall_name or "a required payment"
        date_text = f" on {first_shortfall_date}" if first_shortfall_date else ""
        coverage_message = f"You're short ${float(shortfall or 0):,.0f} for {target}{date_text}."
    elif allowance and (not allowance["known"] or allowance["funding_shortfall"] > 0):
        coverage_status = "unknown" if not allowance["known"] else "savings_shortfall"
        coverage_message = (allowance["verification_reason"]
                            if not allowance["known"] else "The monthly emergency savings plan needs more cash. "
                            "Discretionary spending is paused.")
    elif (not allowance and min(cash, money_state["dated_lowest_after_normal_spending"])
          < money_state["cash_cushion_goal"]):
        coverage_status = "covered_below_cushion"
        coverage_message = "All known bills are covered, but your safety cushion is below target."
    else:
        coverage_status = "covered"
        coverage_message = "All known bills are covered."

    first_income = next(
        (step for step in playbook_cycle if step.get("action") == "deposit_to_checking"),
        None,
    )
    required_before_next_income = round(
        sum(
            float(step.get("amount") or 0)
            for step in playbook_cycle
            if step.get("action") == "pay_from_checking"
            and first_income
            and str(step.get("date") or "")[:10] < str(first_income.get("date") or "")[:10]
        ),
        2,
    )
    extra = float(money_state.get("total_flexible_30_days") or 0)
    if extra > 0:
        extra_reason = (
            "After known bills, normal spending, and your safety cushion, this is left for optional spending."
        )
    elif coverage_status == "shortfall":
        extra_reason = "Your upcoming bills use all available money before planned income arrives."
    elif coverage_status == "unknown":
        extra_reason = "I need the missing payment dates before I can tell you what is safe to spend."
    elif float(money_state.get("safety_cushion") or 0) > 0:
        extra_reason = "Your bills are covered, but the remaining money is currently serving as your safety cushion."
    else:
        extra_reason = "Your upcoming bills and normal spending use the money currently available."

    if allowance and (coverage_status in {"unknown", "shortfall", "savings_shortfall"}
                      or not money_state["cash_rescue"]["normal_spending_entered"]):
        allowance["remaining"] = 0
        allowance["budget"] = 0
        allowance["total_discretionary_planned"] = 0
        for key in ("safe_period_capacity", "retained_per_period", "planned_retained"):
            allowance[key] = 0
        for row in allowance["projection"]:
            row["extraSpend"] = None
        extra = 0
        extra_reason = coverage_message if coverage_status != "covered" else "Enter daily and grocery needs first."
        for key in ("free_to_use_now", "total_flexible_30_days",
                    "discretionary_spending_30_days", "deployable_surplus"):
            money_state[key] = 0
    money_state["today_breakdown"] = {
        "free_to_use_now": money_state["free_to_use_now"],
        "planning_horizon_days": planning_days,
        "planning_through": planning_end.isoformat(),
        "required_planning": round(sum(row.amount for row in events
                                        if row.kind == "obligation" and row.when <= planning_end), 2),
        "normal_spending_planning": round(sum(row["amount"] for row in planned_living_schedule), 2),
        "income_planning": round(sum(row.amount for row in events
                                      if row.kind == "income" and row.when <= planning_end), 2),
        "available_now": round(cash, 2),
        # Misc entries have dates but no required/optional flag. Keep them in
        # scheduled payments rather than silently calling them discretionary.
        "required_payments": round(float(horizon_30.get("obligations_scheduled") or 0), 2),
        "normal_spending": round(float(money_state.get("expected_variable_reserve") or 0), 2),
        "safety_cushion": money_state.get("safety_cushion", 0),
        "cash_cushion_goal": money_state.get("cash_cushion_goal", 0),
        "cash_cushion_current": money_state.get("cash_cushion_current"),
        "cash_cushion_gap": money_state.get("cash_cushion_gap"),
        "cash_cushion_refill": money_state.get("cash_cushion_refill", 0),
        "dated_lowest_after_normal_spending": money_state.get("dated_lowest_after_normal_spending", 0),
        "working_safety_holdback": money_state.get("working_safety_holdback", 0),
        "discretionary_before_refill": money_state.get("discretionary_before_refill", 0),
        "total_flexible_30_days": money_state.get("total_flexible_30_days", 0),
        "discretionary_spending_30_days": money_state.get("discretionary_spending_30_days", 0),
        "discretionary_share": money_state.get("discretionary_share", 0),
        "flexible_safety_remaining": money_state.get("flexible_safety_remaining", 0),
        "extra_safety_holdback": money_state.get("extra_safety_holdback", 0),
        "extra_spending": extra,
        "next_income": round(float(next_pay_amount or 0), 2),
        "next_income_date": next_pay.isoformat() if next_pay else None,
        "required_before_next_income": required_before_next_income,
        "income_30_days": round(float(horizon_30.get("income_expected") or 0), 2),
        "required_30_days": round(float(horizon_30.get("obligations_scheduled") or 0), 2),
        "lowest_balance_30_days": round(float(horizon_30["lowest_balance"]), 2),
        "projected_balance_30_days": round(float(horizon_30["projected_balance"]), 2),
        "coverage_status": coverage_status,
        "coverage_message": coverage_message,
        "extra_reason": extra_reason,
        "first_shortfall_date": first_shortfall_date,
        "first_shortfall_name": first_shortfall_name,
        "missing_due_dates": [
            {"type": item.get("type"), "label": item.get("label"), "missing_field": item.get("missing_field")}
            for item in missing_due_dates
        ],
        "reserve_components": dict(money_state.get("protected_cash_breakdown") or {}),
    }

    recommendations = _build_recommendations(
        playbook_30 or playbook_cycle,
        horizons,
        starting_cash=cash,
        buffer=buffer,
        next_pay=next_pay,
        following_pay=following_pay,
        first_cycle_end=first_cycle_end,
    )
    recommendations.insert(1, forward["summary"])

    if has_shortfall:
        headline = (
            f"{flexible_result['trace']['summary']} Safe to spend is $0 until income or bills change."
        )
    else:
        headline = safe_to_spend_detail(flexible_today, buffer)

    return {
        "starting_cash": round(cash, 2),
        "buffer_target": round(buffer, 2),
        "next_pay_date": next_pay.isoformat() if next_pay else None,
        "following_pay_date": following_pay.isoformat(),
        "pay_cycle_end": first_cycle_end.isoformat(),
        "safe_to_spend": safe_to_spend_now,
        "flexible_today": flexible_today,
        "flexible_today_trace": flexible_result,
        "pay_cycle_safe_to_spend": pay_cycle_outlook["safe_to_spend"],
        "pay_cycle_outlook": pay_cycle_outlook,
        "thirty_day_outlook": forward,
        "flexible_before_next_income": round(
            horizon_30.get("flexible_without_hitting_buffer", 0), 2
        ),
        "checking_cushion_before_next_deposit": round(
            horizon_30.get("flexible_without_hitting_buffer", 0), 2
        ),
        "spendable_after_essentials": spendable_after_essentials,
        "balance_after_pay_cycle": cycle_signed_cash,
        "playbooks": playbooks,
        "playbook": playbooks[DEFAULT_TIMELINE_DAYS],
        "playbook_pay_cycle": playbook_cycle,
        "playbook_timeline_days": DEFAULT_TIMELINE_DAYS,
        "horizons": horizons,
        "lowest_balance": lowest,
        "daily_series": daily_series,
        "canonical_timeline": canonical_timeline,
        "money_state": money_state,
        "recommendations": recommendations,
        "headline": headline,
        "method": "timeline_waterfall",
        "liquidity_diagnostics": diagnostics,
    }


def merge_liquidity_into_cashflow(cf: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Attach planner outputs and prefer timeline-based safe-to-spend for the UI."""
    merged = dict(cf)
    merged["liquidity_plan"] = plan
    merged["money_state"] = plan.get("money_state", {})
    merged["safe_to_spend"] = plan["safe_to_spend"]
    merged["safe_to_spend_timeline"] = plan["safe_to_spend"]
    pay_cycle_safe = plan.get("flexible_today")
    if pay_cycle_safe is None:
        trace = plan.get("flexible_today_trace") or {}
        pay_cycle_safe = trace.get("flexible_today")
    if pay_cycle_safe is None:
        pay_cycle_safe = plan.get("pay_cycle_safe_to_spend")
    merged["safe_to_spend_conservative"] = (
        pay_cycle_safe if pay_cycle_safe is not None else cf.get("safe_to_spend")
    )
    merged["flexible_today"] = merged["safe_to_spend_conservative"]
    merged["flexible_today_trace"] = plan.get("flexible_today_trace")
    merged["pay_cycle_outlook"] = plan.get("pay_cycle_outlook")
    merged["spendable_after_essentials"] = plan["spendable_after_essentials"]
    merged["flexible_before_next_income"] = plan.get("flexible_before_next_income")
    merged["balance_after_pay_cycle"] = plan["balance_after_pay_cycle"]
    merged["playbook"] = plan["playbook"]
    merged["playbooks"] = playbooks_for_api(plan["playbooks"])
    # This is a Python-domain result, so retain the canonical numeric horizon
    # keys for direct callers. FastAPI serializes those keys safely at the API
    # boundary, where JSON object keys are necessarily strings.
    merged["horizons"] = plan["horizons"]
    merged["liquidity_recommendations"] = plan["recommendations"]
    if plan.get("headline"):
        merged["headline"] = plan["headline"]
    if plan.get("thirty_day_outlook"):
        merged["thirty_day_outlook"] = plan["thirty_day_outlook"]
    merged["projected_cash_after_30_days"] = (plan.get("thirty_day_outlook") or {}).get(
        "projected_balance"
    )
    diagnostics = plan.get("liquidity_diagnostics") or {}
    merged["liquidity_diagnostics"] = diagnostics
    if diagnostics.get("warning_count", 0) > 0:
        warnings = list(merged.get("warnings") or [])
        if LIQUIDITY_UNDATED_WARNING not in warnings:
            warnings.append(LIQUIDITY_UNDATED_WARNING)
        merged["warnings"] = warnings
    merged["risk_level"] = _risk_from_plan(cf, plan)
    return merged


def _payment_plan_recommendations(
    playbook: list[dict[str, Any]],
    deposits: list[dict[str, Any]],
    pays: list[dict[str, Any]],
    shortfalls: list[dict[str, Any]],
    *,
    starting_cash: float,
    buffer: float,
    next_pay: date | None,
    following_pay: date | None,
) -> list[str]:
    buffer_note = " and keep your buffer intact" if buffer > 0 else ""
    recs: list[str] = [
        f"Everything runs through checking — you have ${starting_cash:,.0f} now; "
        f"pay in due-date order{buffer_note}.",
    ]
    if deposits:
        d0 = deposits[0]
        recs.append(
            f"Next deposit ${float(d0.get('amount') or 0):,.0f} on {d0.get('date')} ({d0.get('name')})."
        )
    if pays:
        names = ", ".join(f"{p['name']} (${float(p.get('amount') or 0):,.0f})" for p in pays[:4])
        recs.append(f"Scheduled payments: {names}.")
    if shortfalls:
        recs.append(shortfalls[0].get("detail") or "Resolve the first cash shortfall before extras.")
    if next_pay and following_pay:
        recs.append(f"Pay cycle deposits: {next_pay.isoformat()} then {following_pay.isoformat()}.")
    recs.append(f"Keep ${buffer:,.0f} in checking as your minimum buffer.")
    return recs[:8]


def _month_timeline(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Single chronological timeline for the briefing UI (income before outflows same day)."""
    items: list[dict[str, Any]] = []
    for s in steps:
        action = s.get("action")
        if action == "shortfall":
            items.append({**s, "step_kind": "alert"})
        elif action in ("deposit_to_checking", "receive_income"):
            items.append({**s, "step_kind": "in"})
        elif action == "pay_from_checking":
            items.append({**s, "step_kind": "out"})
    items.sort(
        key=lambda s: (
            s.get("date") or "",
            0 if s.get("step_kind") == "in" else 1,
            s.get("name") or "",
        )
    )
    return items


def build_cfo_payment_plan(session: Session, *, days: int = 30) -> dict[str, Any]:
    """Deterministic pay-on-time plan for daily briefing (30-day waterfall only — fast path)."""
    from app.core.cashflow_engine import calculate_cashflow

    today = date.today()
    end = today + timedelta(days=days)
    cash = _resolve_checking_balance(session, None)
    buffer = _effective_buffer(session)
    cf = calculate_cashflow(session, cash)
    safe = float(cf.get("safe_to_spend") or 0)
    sources = list(session.exec(select(IncomeSource)).all())
    next_pay, _ = _next_pay_date(sources, today)
    following_pay = _following_pay_date(sources, next_pay) if next_pay else today + timedelta(days=28)

    events, _ = _collect_events(session, end)
    _, window_steps, _ = _simulate_waterfall(cash, events, through=end)

    pays = [s for s in window_steps if s.get("action") == "pay_from_checking"]
    shortfalls = [s for s in window_steps if s.get("action") == "shortfall"]
    deposits = [s for s in window_steps if s.get("action") == "deposit_to_checking"]

    total_due = round(sum(float(p.get("amount") or 0) for p in pays), 2)
    starting = float(cash)

    outlook = cf.get("thirty_day_outlook") or {}
    if shortfalls:
        status = "shortfall"
    elif outlook.get("status") == "shortfall":
        status = "shortfall"
    elif outlook.get("status") == "tight" or safe <= 0:
        status = "tight"
    elif safe < buffer:
        status = "tight"
    else:
        status = "ok"

    week_end = (today + timedelta(days=7)).isoformat()
    pays_this_week = [p for p in pays if (p.get("date") or "")[:10] <= week_end]
    pays_later = [p for p in pays if (p.get("date") or "")[:10] > week_end]

    strategy_parts: list[str] = []
    if outlook.get("summary"):
        strategy_parts.append(str(outlook["summary"]))
    if shortfalls:
        s0 = shortfalls[0]
        gap = float(s0.get("shortfall") or 0)
        strategy_parts.append(
            f"Critical: {s0.get('name')} needs ${float(s0.get('amount') or 0):,.0f} on {s0.get('date')} "
            f"— ${gap:,.0f} short even with planned paychecks. Fix this before any extras."
        )
    pool_line = (
        f"Run checking (${starting:,.0f}) as one pool: pay {len(pays)} items (${total_due:,.0f}) "
        f"in due-date order"
    )
    if buffer > 0:
        pool_line += f" and keep a ${buffer:,.0f} buffer (${safe:,.0f} safe to spend after essentials)."
    else:
        pool_line += f" — ${safe:,.0f} safe to spend after essentials."
    strategy_parts.append(pool_line)
    if deposits:
        d0 = deposits[0]
        strategy_parts.append(
            f"Deposit ${float(d0.get('amount') or 0):,.0f} on {d0.get('date')} ({d0.get('name')}) "
            "before relying on that cash for later-dated bills."
        )
    if pays_this_week and pays_later:
        week_total = sum(float(p.get("amount") or 0) for p in pays_this_week)
        later_total = sum(float(p.get("amount") or 0) for p in pays_later)
        strategy_parts.append(
            f"This week: cover {len(pays_this_week)} payment(s) (${week_total:,.0f}). "
            f"Hold planning for {len(pays_later)} later payment(s) (${later_total:,.0f}) until their due dates "
            "unless your next deposit covers them."
        )

    phases: list[dict[str, Any]] = []
    if pays_this_week:
        phases.append({
            "phase": "this_week",
            "title": "Pay from checking now",
            "items": [
                {
                    "date": p.get("date"),
                    "name": p.get("name"),
                    "amount": p.get("amount"),
                    "category": p.get("category"),
                    "balance_after": p.get("balance_after"),
                    "due_date": p.get("due_date"),
                    "bill_id": p.get("bill_id"),
                    "debt_id": p.get("debt_id"),
                    "can_mark_paid": p.get("can_mark_paid"),
                }
                for p in pays_this_week
            ],
        })
    if deposits:
        phases.append({
            "phase": "income",
            "title": "Expected deposits",
            "items": [
                {
                    "date": d.get("date"),
                    "name": d.get("name"),
                    "amount": d.get("amount"),
                    "category": "income",
                }
                for d in deposits
            ],
        })
    if pays_later:
        phases.append({
            "phase": "later_this_month",
            "title": f"Later this month ({len(pays_later)} payments)",
            "items": [
                {
                    "date": p.get("date"),
                    "name": p.get("name"),
                    "amount": p.get("amount"),
                    "category": p.get("category"),
                    "balance_after": p.get("balance_after"),
                    "due_date": p.get("due_date"),
                    "bill_id": p.get("bill_id"),
                    "debt_id": p.get("debt_id"),
                    "can_mark_paid": p.get("can_mark_paid"),
                }
                for p in pays_later
            ],
        })

    return {
        "horizon_days": days,
        "status": status,
        "starting_cash": round(starting, 2),
        "safe_to_spend": round(safe, 2),
        "thirty_day_outlook": outlook,
        "buffer_target": round(buffer, 2),
        "next_pay_date": next_pay.isoformat() if next_pay else None,
        "following_pay_date": following_pay.isoformat(),
        "strategy_summary": " ".join(strategy_parts),
        "recommendations": _payment_plan_recommendations(
            window_steps,
            deposits,
            pays,
            shortfalls,
            starting_cash=starting,
            buffer=buffer,
            next_pay=next_pay,
            following_pay=following_pay,
        ),
        "timeline": window_steps,
        "month_timeline": _month_timeline(window_steps),
        "phases": phases,
        "payment_count": len(pays),
        "payments_this_week": len(pays_this_week),
        "payments_later": len(pays_later),
        "total_scheduled": total_due,
        "shortfalls": shortfalls,
    }


def _risk_from_plan(cf: dict[str, Any], plan: dict[str, Any]) -> str:
    flex_trace = plan.get("flexible_today_trace") or {}
    if flex_trace.get("has_shortfall"):
        return "danger"
    outlook = plan.get("thirty_day_outlook") or {}
    status = outlook.get("status")
    if status == "shortfall" or any(
        s["action"] == "shortfall" for s in plan.get("playbook", [])
    ):
        return "danger"
    if status == "tight":
        return "tight"
    safe = float(plan.get("flexible_today", plan.get("safe_to_spend", 0)))
    buffer = float(plan.get("buffer_target", 0))
    if safe <= 0:
        return "danger"
    if safe < buffer * 2:
        return "tight"
    return "safe"
