"""Compare user-submitted offers against accounts, benchmarks, cashflow, and DTI."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, select

from app.core.data_quality import assess_data_quality
from app.core.mortgage_optimizer import analyze_mortgage
from app.core.offer_intake import INTAKE_PENDING, offer_to_dict
from app.core.offer_intake_validation import is_offer_expired
from app.core.opportunity_research import build_research_context_from_snapshot
from app.core.research_approval import build_offers_from_approved_curated
from app.db.models import Bill, CreditCard, Debt, FinancialOffer, MortgageAccount
from app.events.bus import event_bus
from app.events.types import Event, EventType
from app.research.providers.curated_rates import _load_curated

VERDICT_REJECT = "reject"
VERDICT_MONITOR = "monitor"
VERDICT_COMPARE = "compare"
VERDICT_STRONG = "strong_candidate"

# Debt-reduction offers can still be worth comparing when cashflow is tight.
_DEBT_SAVINGS_PRODUCTS = frozenset(
    {
        "balance_transfer",
        "credit_card",
        "personal_loan",
        "auto_refinance",
        "mortgage_refinance",
    }
)


def compare_offer(session: Session, offer_id: str) -> dict[str, Any]:
    """Run deterministic comparison; persist result on offer.comparison_json."""
    offer = session.get(FinancialOffer, offer_id)
    if not offer:
        raise ValueError("offer not found")

    if is_offer_expired(offer):
        result = _comparison_result(
            offer,
            verdict=VERDICT_REJECT,
            risks=["Offer expiration date has passed"],
            recommended_next_step="Do not apply — offer expired",
        )
        _save_comparison(session, offer, result)
        return result

    from app.core.evidence import offer_source_proof_satisfied

    if not offer_source_proof_satisfied(session, offer):
        result = _comparison_result(
            offer,
            verdict=VERDICT_REJECT,
            risks=["Missing source URL or evidence"],
            recommended_next_step=(
                "Add issuer page URL, paste offer evidence, or upload and approve a proof file"
            ),
        )
        _save_comparison(session, offer, result)
        return result

    from app.core.financial_snapshot import build_financial_snapshot

    snapshot = build_financial_snapshot(session)
    ctx = build_research_context_from_snapshot(snapshot)
    benchmarks = build_offers_from_approved_curated(ctx)
    curated = _load_curated() or {}

    cf = snapshot.cashflow
    dti = snapshot.dti
    dq = assess_data_quality(session)

    product = offer.product_type
    assumptions: list[str] = []
    risks: list[str] = []
    benchmark_used: list[dict[str, Any]] = []
    dq_warnings = [i.get("message", "") for i in dq.get("issues", []) if i.get("severity") in ("warn", "error")]

    monthly_delta = 0.0
    total_delta = 0.0
    break_even_months: int | None = None

    if product in ("balance_transfer", "credit_card"):
        monthly_delta, total_delta, break_even_months, benchmark_used, risks, assumptions = _compare_credit(
            session, offer, benchmarks, assumptions, risks
        )
    elif product in ("personal_loan", "auto_refinance"):
        monthly_delta, total_delta, benchmark_used, risks, assumptions = _compare_loan(
            session, offer, curated, product, assumptions, risks
        )
    elif product == "mortgage_refinance":
        monthly_delta, total_delta, break_even_months, benchmark_used, risks, assumptions = _compare_mortgage(
            session, offer, benchmarks, assumptions, risks
        )
    elif product == "insurance":
        monthly_delta, total_delta, benchmark_used, risks, assumptions = _compare_insurance(
            session, offer, curated, assumptions, risks
        )
    elif product in ("hysa", "cd"):
        monthly_delta, total_delta, benchmark_used, assumptions = _compare_savings(
            offer, curated, ctx, assumptions
        )
    elif product == "utility":
        monthly_delta, total_delta, benchmark_used, assumptions = _compare_utility(
            session, offer, assumptions
        )
    else:
        assumptions.append(f"No specialized comparator for {product}; using savings estimate only")
        total_delta = float(offer.estimated_savings or 0)
        monthly_delta = total_delta / 12

    verdict = _score_verdict(
        offer=offer,
        monthly_delta=monthly_delta,
        total_delta=total_delta,
        break_even_months=break_even_months,
        cashflow=cf,
        dti=dti,
        dq=dq,
        risks=risks,
    )

    if dq.get("blocking") and verdict == VERDICT_STRONG:
        verdict = VERDICT_COMPARE
        dq_warnings.append("Data quality blocking issues — verify household data before applying")

    if offer.requires_credit_check and dti.get("status") in ("constrained", "high-risk"):
        risks.append(f"DTI {dti.get('total_dti')}% may limit approval")
        if verdict == VERDICT_STRONG:
            verdict = VERDICT_COMPARE

    if cf.get("risk_level") == "danger" and monthly_delta < 0:
        verdict = VERDICT_REJECT
        risks.append("Cashflow already in danger zone — offer worsens monthly cashflow")

    recommended = _next_step(verdict, product, break_even_months)

    result = _comparison_result(
        offer,
        verdict=verdict,
        estimated_monthly_delta=round(monthly_delta, 2),
        estimated_total_delta=round(total_delta, 2),
        break_even_months=break_even_months,
        risks=risks,
        assumptions=assumptions,
        benchmark_used=benchmark_used,
        data_quality_warnings=dq_warnings[:8],
        recommended_next_step=recommended,
    )

    _save_comparison(session, offer, result)

    event_bus.emit(
        Event(
            event_type=EventType.OFFER_COMPARED.value,
            source="offer.comparison",
            payload={"offer_id": offer.id, "verdict": verdict},
        )
    )
    if verdict == VERDICT_STRONG:
        event_bus.emit(
            Event(
                event_type=EventType.OFFER_STRONG_CANDIDATE.value,
                source="offer.comparison",
                payload={"offer_id": offer.id, "product_type": product},
            )
        )

    return result


def _comparison_result(offer: FinancialOffer, *, verdict: str, **kwargs: Any) -> dict[str, Any]:
    base = {
        "offer_id": offer.id,
        "verdict": verdict,
        "estimated_monthly_delta": kwargs.get("estimated_monthly_delta", 0),
        "estimated_total_delta": kwargs.get("estimated_total_delta", 0),
        "break_even_months": kwargs.get("break_even_months"),
        "risks": kwargs.get("risks", []),
        "assumptions": kwargs.get("assumptions", []),
        "benchmark_used": kwargs.get("benchmark_used", []),
        "data_quality_warnings": kwargs.get("data_quality_warnings", []),
        "recommended_next_step": kwargs.get("recommended_next_step", ""),
        "compared_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    return base


def _save_comparison(session: Session, offer: FinancialOffer, result: dict[str, Any]) -> None:
    offer.comparison_json = json.dumps(result, default=str)
    offer.estimated_savings = max(0.0, float(result.get("estimated_total_delta") or 0))
    offer.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(offer)
    session.commit()


def _compare_credit(
    session: Session,
    offer: FinancialOffer,
    benchmarks: list[dict[str, Any]],
    assumptions: list[str],
    risks: list[str],
) -> tuple[float, float, int | None, list[dict[str, Any]], list[str], list[str]]:
    benchmark_used: list[dict[str, Any]] = []
    card = _resolve_credit_card(session, offer)
    if not card:
        risks.append("No credit card on file — link a card or add card balance/APR")
        return 0.0, 0.0, None, [], risks, assumptions

    promo_apr = float(offer.estimated_apr or 0)
    fee_pct = float(offer.balance_transfer_fee_percent if offer.balance_transfer_fee_percent is not None else 3)
    balance = float(card.balance)
    current_apr = float(card.apr)

    annual_current = balance * (current_apr / 100)
    annual_promo = balance * (promo_apr / 100)
    fee = balance * (fee_pct / 100)
    first_year_net = annual_current - annual_promo - fee
    monthly_delta = first_year_net / 12

    for b in benchmarks:
        if b.get("product_type") in ("balance_transfer", "credit_card_benchmark"):
            benchmark_used.append(
                {"type": "curated_benchmark", "apr": b.get("estimated_apr"), "source": b.get("source_url")}
            )
            break

    assumptions.append(f"Compared to {card.issuer} balance ${balance:.0f} at {current_apr}% APR")
    assumptions.append(f"Transfer fee assumed {fee_pct}% (${fee:.2f})")

    if first_year_net <= 0:
        risks.append("Promo APR and fee do not beat current interest cost in year one")

    return monthly_delta, first_year_net, None, benchmark_used, risks, assumptions


def _compare_loan(
    session: Session,
    offer: FinancialOffer,
    curated: dict[str, Any],
    product: str,
    assumptions: list[str],
    risks: list[str],
) -> tuple[float, float, list[dict], list[str], list[str]]:
    debt = _resolve_debt(session, offer)
    benchmark_used: list[dict] = []
    key = "auto_loan_rates" if product == "auto_refinance" else "personal_loan_rates"
    bench = curated.get(key) or {}
    if bench.get("typical_apr_percent") is not None:
        benchmark_used.append(
            {
                "type": key,
                "apr": bench["typical_apr_percent"],
                "source": bench.get("source_url"),
            }
        )

    new_apr = float(offer.estimated_apr or 0)
    new_payment = float(offer.monthly_payment_estimate or 0)
    origination = float(offer.origination_fee or 0)

    if debt:
        old_payment = float(debt.minimum_payment)
        old_apr = float(debt.apr)
        monthly_delta = old_payment - new_payment
        assumptions.append(f"Compared to debt {debt.creditor}: ${old_payment}/mo at {old_apr}%")
        if new_apr >= old_apr:
            risks.append("Offer APR not below current debt APR")
    else:
        monthly_delta = float(offer.estimated_savings or 0) / 12
        assumptions.append("No linked debt — using estimated_savings only")

    total_delta = monthly_delta * 12 - origination
    if origination > 0:
        assumptions.append(f"Origination fee ${origination:.2f} deducted from first-year benefit")

    return monthly_delta, total_delta, benchmark_used, risks, assumptions


def _compare_mortgage(
    session: Session,
    offer: FinancialOffer,
    benchmarks: list[dict[str, Any]],
    assumptions: list[str],
    risks: list[str],
) -> tuple[float, float, int | None, list[dict], list[str], list[str]]:
    mortgage = _resolve_mortgage(session, offer)
    market_rate = float(offer.estimated_apr or 0)
    closing = float(offer.closing_costs if offer.closing_costs is not None else 5000)
    benchmark_used: list[dict[str, Any]] = []

    for b in benchmarks:
        if b.get("product_type", "").startswith("mortgage"):
            market_rate = market_rate or float(b.get("estimated_apr") or 0)
            benchmark_used.append(
                {"type": "mortgage_benchmark", "apr": b.get("estimated_apr"), "source": b.get("source_url")}
            )
            break

    if not mortgage:
        risks.append("No mortgage on file — add mortgage or link account")
        return 0.0, 0.0, None, benchmark_used, risks, assumptions

    analysis = analyze_mortgage(mortgage, estimated_market_rate=market_rate, closing_cost_assumption=closing)
    monthly_delta = float(analysis.get("monthly_savings_estimate") or 0)
    break_even = analysis.get("break_even_months")
    if break_even is None or break_even == 0:
        be: int | None = None
    else:
        be = int(break_even)

    if be is None and monthly_delta > 0 and closing > 0:
        be = int(closing / monthly_delta)

    if be is None and monthly_delta > 0:
        risks.append("Break-even months could not be calculated — provide closing_costs")
    elif be and be > 60:
        risks.append(f"Break-even {be} months exceeds typical refinance window")

    assumptions.append(analysis.get("why", "Mortgage refinance analysis"))
    total_delta = monthly_delta * 12 - closing

    return monthly_delta, total_delta, be, benchmark_used, risks, assumptions


def _compare_insurance(
    session: Session,
    offer: FinancialOffer,
    curated: dict[str, Any],
    assumptions: list[str],
    risks: list[str],
) -> tuple[float, float, list[dict], list[str], list[str]]:
    bill = _resolve_insurance_bill(session, offer)
    new_monthly = float(offer.monthly_payment_estimate or 0)
    benchmark_used: list[dict] = []
    med = (curated.get("insurance_median_premium_monthly") or {}).get("auto")
    if med is not None:
        benchmark_used.append({"type": "insurance_median", "monthly": med})

    if bill:
        current = float(bill.amount)
        monthly_delta = current - new_monthly
        assumptions.append(f"Compared to bill {bill.name}: ${current:.2f}/mo")
        if new_monthly >= current:
            risks.append("Quoted premium not below current bill")
    else:
        monthly_delta = float(offer.estimated_savings or 0) / 12
        assumptions.append("No insurance bill matched — using estimated savings")

    return monthly_delta, monthly_delta * 12, benchmark_used, risks, assumptions


def _compare_savings(
    offer: FinancialOffer,
    curated: dict[str, Any],
    ctx: dict[str, Any],
    assumptions: list[str],
) -> tuple[float, float, list[dict], list[str]]:
    benchmark_used: list[dict] = []
    hysa = (curated.get("hysa") or [{}])[0]
    bench_apy = float(hysa.get("apy") or 0)
    if bench_apy:
        benchmark_used.append({"type": "hysa_benchmark", "apy": bench_apy, "source": hysa.get("source_url")})

    offer_apy = float(offer.apy or 0)
    emergency = 0.0
    if ctx.get("borrower"):
        emergency = float(ctx["borrower"].get("emergency_fund_balance") or 0)

    delta_apy = offer_apy - bench_apy
    annual_extra = emergency * (delta_apy / 100) if emergency > 0 else 0
    assumptions.append(
        f"APY {offer_apy}% vs benchmark {bench_apy}% on ${emergency:.0f} savings balance"
    )
    return annual_extra / 12, annual_extra, benchmark_used, assumptions


def _compare_utility(
    session: Session,
    offer: FinancialOffer,
    assumptions: list[str],
) -> tuple[float, float, list[dict], list[str]]:
    bill = _resolve_utility_bill(session, offer)
    new_monthly = float(offer.monthly_payment_estimate or 0)
    if bill:
        current = float(bill.amount)
        monthly_delta = current - new_monthly
        assumptions.append(f"Compared to {bill.name}: ${current:.2f}/mo")
    else:
        monthly_delta = float(offer.estimated_savings or 0) / 12
        assumptions.append("No utility bill matched — using estimated savings")
    return monthly_delta, monthly_delta * 12, [], assumptions


def _score_verdict(
    *,
    offer: FinancialOffer,
    monthly_delta: float,
    total_delta: float,
    break_even_months: int | None,
    cashflow: dict[str, Any],
    dti: dict[str, Any],
    dq: dict[str, Any],
    risks: list[str],
) -> str:
    if monthly_delta < -5:
        return VERDICT_REJECT
    if total_delta <= 0 and not offer.estimated_savings:
        return VERDICT_REJECT

    if offer.product_type == "mortgage_refinance":
        if break_even_months is None:
            return VERDICT_MONITOR
        if break_even_months > 48:
            return VERDICT_MONITOR

    if dq.get("blocking"):
        return VERDICT_MONITOR

    if cashflow.get("risk_level") == "danger":
        if offer.product_type not in _DEBT_SAVINGS_PRODUCTS or total_delta <= 0:
            return VERDICT_MONITOR

    if dti.get("status") == "high-risk" and offer.requires_credit_check:
        return VERDICT_MONITOR

    if total_delta >= 500 or (offer.estimated_savings or 0) >= 500:
        if not risks and dti.get("status") not in ("high-risk",):
            return VERDICT_STRONG
        return VERDICT_COMPARE

    if total_delta > 0 or monthly_delta > 10:
        return VERDICT_COMPARE

    return VERDICT_MONITOR


def _next_step(verdict: str, product: str, break_even: int | None) -> str:
    if verdict == VERDICT_REJECT:
        return "Do not apply — costs or risks outweigh benefit"
    if verdict == VERDICT_MONITOR:
        if product == "mortgage_refinance" and break_even:
            return f"Monitor rates; break-even ~{break_even} months — re-run when closing costs confirmed"
        return "Gather more data or wait for better terms"
    if verdict == VERDICT_STRONG:
        return "Strong candidate — approve offer intake then verify terms with issuer before applying"
    return "Compare fine print with current account; approve intake if terms verified"


def _resolve_credit_card(session: Session, offer: FinancialOffer) -> CreditCard | None:
    if offer.linked_account_id and offer.linked_account_type == "credit_card":
        return session.get(CreditCard, offer.linked_account_id)
    cards = list(session.exec(select(CreditCard)).all())
    if not cards:
        return None
    return max(cards, key=lambda c: float(c.balance) * float(c.apr))


def _resolve_debt(session: Session, offer: FinancialOffer) -> Debt | None:
    if offer.linked_account_id and offer.linked_account_type == "debt":
        return session.get(Debt, offer.linked_account_id)
    debts = list(session.exec(select(Debt)).all())
    return max(debts, key=lambda d: float(d.apr), default=None) if debts else None


def _resolve_mortgage(session: Session, offer: FinancialOffer) -> MortgageAccount | None:
    if offer.linked_account_id and offer.linked_account_type == "mortgage":
        return session.get(MortgageAccount, offer.linked_account_id)
    return session.exec(select(MortgageAccount)).first()


def _resolve_insurance_bill(session: Session, offer: FinancialOffer) -> Bill | None:
    if offer.linked_account_id and offer.linked_account_type == "bill":
        return session.get(Bill, offer.linked_account_id)
    for bill in session.exec(select(Bill)).all():
        cat = (bill.category or "").lower()
        name = (bill.name or "").lower()
        if "insurance" in cat or "insurance" in name:
            return bill
    return None


def _resolve_utility_bill(session: Session, offer: FinancialOffer) -> Bill | None:
    if offer.linked_account_id and offer.linked_account_type == "bill":
        linked = session.get(Bill, offer.linked_account_id)
        if linked:
            return linked
    keywords = ("internet", "phone", "utility", "cable", "wireless", "broadband")
    for bill in session.exec(select(Bill)).all():
        name = (bill.name or "").lower()
        cat = (bill.category or "").lower()
        if any(k in name or k in cat for k in keywords):
            return bill
    return None


def list_comparable_offers(session: Session) -> list[dict[str, Any]]:
    """Offers visible in profile list — approved user + research; excludes pending intake."""
    from app.core.offer_intake import SOURCE_RESEARCH, SOURCE_USER

    rows = list(session.exec(select(FinancialOffer)).all())
    out: list[dict[str, Any]] = []
    for o in rows:
        if o.intake_source == SOURCE_USER and o.intake_status == INTAKE_PENDING:
            continue
        if o.intake_source == SOURCE_USER:
            out.append(offer_to_dict(o))
        elif o.intake_source == SOURCE_RESEARCH:
            out.append(_research_row(o))
    return sorted(out, key=lambda x: -(float(x.get("estimated_savings") or 0)))


def _research_row(o: FinancialOffer) -> dict[str, Any]:
    return {
        "id": o.id,
        "provider": o.provider,
        "product": o.product_type,
        "estimated_savings": o.estimated_savings,
        "eligibility_fit": "strong" if o.confidence >= 0.8 else "possible",
        "confidence": o.confidence,
        "source": o.source_url or "research",
    }
