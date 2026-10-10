"""Insurance premium benchmark vs user bills (curated median, deterministic)."""

import json
from typing import Any

from app.research.providers.base import ResearchOffer, ResearchProvider, ResearchProviderResult
from app.research.providers.curated_rates import curated_path


class InsuranceBenchmarkProvider(ResearchProvider):
    provider_id = "insurance_benchmark"
    product_types = ["insurance"]

    def fetch(self, context: dict[str, Any]) -> ResearchProviderResult:
        data = _load_curated()
        if not data:
            return ResearchProviderResult(provider_id=self.provider_id, error="curated_rates.json missing")

        state = "default"
        if context.get("borrower") and context["borrower"].get("state"):
            state = context["borrower"]["state"].upper()[:2]

        medians = data.get("insurance_by_state", {}).get(state) or data.get("insurance_by_state", {}).get(
            "default", {}
        )
        auto_median = float(
            medians.get("auto_monthly_median")
            or data.get("insurance_median_premium_monthly", {}).get("auto", 0)
        )
        if auto_median <= 0:
            return ResearchProviderResult(provider_id=self.provider_id, skipped=True)

        offers = []
        for bill in context.get("raw_bills") or []:
            cat = (bill.get("category") or "").lower()
            name = (bill.get("name") or "").lower()
            if "insurance" not in cat and "insurance" not in name:
                continue
            amount = float(bill.get("amount", 0))
            if amount <= 0:
                continue
            pct_above = round((amount - auto_median) / auto_median * 100, 1) if auto_median else 0
            if pct_above >= 15:
                offers.append(
                    ResearchOffer(
                        provider="insurance_benchmark",
                        product_type="insurance",
                        estimated_savings=round((amount - auto_median) * 12, 2),
                        eligibility_notes=(
                            f"{bill.get('name')}: ${amount:.0f}/mo vs median ~${auto_median:.0f}/mo "
                            f"({pct_above}% above)"
                        ),
                        source_url=data.get("insurance_median_premium_monthly", {}).get("source_url", ""),
                        confidence=float(data.get("insurance_median_premium_monthly", {}).get("confidence", 0.4)),
                        raw_source="curated insurance median",
                        math={
                            "bill_id": bill.get("id"),
                            "your_monthly": amount,
                            "median_monthly": auto_median,
                            "pct_above_median": pct_above,
                            "state": state,
                        },
                        warnings=[
                            "Median is illustrative — request comparison quotes for your profile",
                            "Requires manual verification",
                        ],
                    )
                )

        if not offers:
            return ResearchProviderResult(provider_id=self.provider_id, skipped=True)

        return ResearchProviderResult(provider_id=self.provider_id, offers=offers)


def _load_curated() -> dict[str, Any] | None:
    path = curated_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
