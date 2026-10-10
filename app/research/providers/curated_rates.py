"""Curated HYSA/CD and credit benchmarks from shipped JSON (sourced, not runtime-fabricated)."""

import json
import os
from pathlib import Path
from typing import Any

from app.config import ROOT_DIR
from app.research.providers.base import ResearchOffer, ResearchProvider, ResearchProviderResult


def curated_path() -> Path:
    env = os.getenv("RESEARCH_CURATED_PATH", "").strip()
    if env:
        return Path(env)
    return ROOT_DIR / "data" / "research" / "curated_rates.json"


CURATED_PATH = curated_path()


class CuratedHysaProvider(ResearchProvider):
    provider_id = "curated_hysa"
    product_types = ["hysa", "cd"]

    def fetch(self, context: dict[str, Any]) -> ResearchProviderResult:
        data = _load_curated()
        if not data:
            return ResearchProviderResult(
                provider_id=self.provider_id,
                error="curated_rates.json missing or invalid",
            )

        offers = []
        emergency = 0.0
        if context.get("borrower"):
            emergency = float(context["borrower"].get("emergency_fund_balance", 0))

        for entry in data.get("hysa", []):
            apy = float(entry["apy"])
            offers.append(
                ResearchOffer(
                    provider=entry["provider"],
                    product_type="hysa",
                    estimated_apr=apy,
                    estimated_savings=round(emergency * (apy / 100), 2) if emergency > 0 else 0,
                    eligibility_notes=entry.get("notes", ""),
                    source_url=entry.get("source_url", ""),
                    confidence=float(entry.get("confidence", 0.7)),
                    raw_source=entry.get("source_label", "curated_rates.json"),
                    math={"apy": apy, "as_of": entry.get("as_of"), "on_balance": emergency},
                    warnings=entry.get("warnings", []),
                )
            )
        return ResearchProviderResult(provider_id=self.provider_id, offers=offers)


class CuratedCreditBenchmarkProvider(ResearchProvider):
    provider_id = "curated_credit_benchmark"
    product_types = ["balance_transfer", "credit_card_benchmark"]

    def fetch(self, context: dict[str, Any]) -> ResearchProviderResult:
        data = _load_curated()
        if not data:
            return ResearchProviderResult(provider_id=self.provider_id, error="curated_rates.json missing")

        bench = data.get("balance_transfer_benchmark", {})
        promo_apr = float(bench.get("typical_promo_apr_percent", 0))
        fee_pct = float(bench.get("typical_transfer_fee_percent", 3))
        offers = []

        for card in context.get("raw_credit_cards") or []:
            balance = float(card.get("balance", 0))
            apr = float(card.get("apr", 0))
            if balance <= 0 or apr <= promo_apr:
                continue
            annual_interest = balance * (apr / 100)
            promo_interest = balance * (promo_apr / 100)
            fee = balance * (fee_pct / 100)
            net_savings = annual_interest - promo_interest - fee
            if net_savings > 0:
                offers.append(
                    ResearchOffer(
                        provider=bench.get("source_label", "curated benchmark"),
                        product_type="balance_transfer",
                        estimated_apr=promo_apr,
                        estimated_savings=round(net_savings, 2),
                        eligibility_notes=f"vs {card.get('issuer')} at {apr}% APR",
                        source_url=bench.get("source_url", ""),
                        confidence=float(bench.get("confidence", 0.55)),
                        raw_source="curated balance_transfer_benchmark",
                        math={
                            "card_id": card.get("id"),
                            "current_apr": apr,
                            "promo_apr": promo_apr,
                            "transfer_fee_pct": fee_pct,
                            "yearly_interest_current": round(annual_interest, 2),
                            "yearly_interest_promo": round(promo_interest, 2),
                            "transfer_fee": round(fee, 2),
                            "net_first_year_savings": round(net_savings, 2),
                        },
                        warnings=["Verify promo period length and post-promo APR before applying"],
                    )
                )

        return ResearchProviderResult(provider_id=self.provider_id, offers=offers)


def _load_curated() -> dict[str, Any] | None:
    path = curated_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
