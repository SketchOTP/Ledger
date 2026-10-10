"""FRED 30-year mortgage rate — real public data (requires FRED_API_KEY)."""

from typing import Any

from app.research.providers.base import ResearchOffer, ResearchProvider, ResearchProviderResult
from app.research.providers.fred_series import (
    fetch_fred_group,
    fred_api_key,
    fred_series_url,
)


class FredMortgageProvider(ResearchProvider):
    provider_id = "fred_mortgage"
    product_types = ["mortgage_rate", "mortgage_refinance"]
    SERIES_ID = "MORTGAGE30US"

    def fetch(self, context: dict[str, Any]) -> ResearchProviderResult:
        if not fred_api_key():
            return ResearchProviderResult(
                provider_id=self.provider_id,
                skipped=True,
                error="FRED_API_KEY not set — mortgage market rate unavailable",
            )

        group = fetch_fred_group("mortgage_rates")
        if group.get("error") or group.get("rate_percent") is None:
            return ResearchProviderResult(
                provider_id=self.provider_id,
                error=group.get("error", "FRED mortgage fetch failed"),
            )

        rate = float(group["rate_percent"])
        obs_date = group.get("observation_date", "")
        rate_15 = group.get("rate_15yr")
        return self._build_result(context, rate, obs_date, rate_15yr=rate_15)

    def _build_result(
        self,
        context: dict[str, Any],
        rate: float,
        obs_date: str,
        *,
        rate_15yr: float | None = None,
    ) -> ResearchProviderResult:
        mortgages = context.get("raw_mortgages") or []
        offers: list[ResearchOffer] = []
        for m in mortgages:
            current = float(m.get("interest_rate", 0))
            if current <= 0:
                continue
            savings_note = ""
            monthly_savings = 0.0
            if rate < current:
                balance = float(m.get("current_balance", 0))
                term = int(m.get("remaining_term_months") or 360)
                r_old, r_new = current / 100 / 12, rate / 100 / 12
                if r_old > 0 and balance > 0:
                    old_p = balance * (r_old * (1 + r_old) ** term) / ((1 + r_old) ** term - 1)
                    new_p = balance * (r_new * (1 + r_new) ** term) / ((1 + r_new) ** term - 1)
                    monthly_savings = max(0, old_p - new_p)
                savings_note = f"Market {rate}% vs your {current}%"

            math: dict[str, Any] = {
                "market_rate": rate,
                "your_rate": current,
                "observation_date": obs_date,
                "monthly_savings_estimate": round(monthly_savings, 2),
            }
            if rate_15yr is not None:
                math["market_rate_15yr"] = rate_15yr

            offers.append(
                ResearchOffer(
                    provider="FRED",
                    product_type="mortgage_refinance",
                    estimated_apr=rate,
                    estimated_savings=round(monthly_savings * 12, 2) if monthly_savings else 0,
                    monthly_payment_estimate=round(monthly_savings, 2) if monthly_savings else None,
                    eligibility_notes=savings_note or f"National avg 30yr fixed as of {obs_date}",
                    source_url=fred_series_url(self.SERIES_ID),
                    confidence=0.85,
                    raw_source="FRED MORTGAGE30US",
                    math=math,
                    warnings=[] if rate < current else ["Market rate not below your current rate"],
                )
            )

        if not mortgages:
            math = {"market_rate": rate, "observation_date": obs_date}
            if rate_15yr is not None:
                math["market_rate_15yr"] = rate_15yr
            offers.append(
                ResearchOffer(
                    provider="FRED",
                    product_type="mortgage_rate",
                    estimated_apr=rate,
                    eligibility_notes=f"National 30yr fixed mortgage avg ({obs_date})",
                    source_url=fred_series_url(self.SERIES_ID),
                    confidence=0.85,
                    raw_source="FRED MORTGAGE30US",
                    math=math,
                )
            )

        return ResearchProviderResult(provider_id=self.provider_id, offers=offers)
