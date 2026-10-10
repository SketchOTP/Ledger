"""Utility/internet bill awareness — flags high recurring utilities vs user baseline."""

from typing import Any

from app.research.providers.base import ResearchOffer, ResearchProvider, ResearchProviderResult

UTILITY_KEYWORDS = ("electric", "gas", "water", "internet", "phone", "mobile", "utility", "cable")


class UtilityBenchmarkProvider(ResearchProvider):
    provider_id = "utility_benchmark"
    product_types = ["utility"]

    def fetch(self, context: dict[str, Any]) -> ResearchProviderResult:
        utility_bills = []
        for bill in context.get("raw_bills") or []:
            text = f"{bill.get('name', '')} {bill.get('category', '')}".lower()
            if any(k in text for k in UTILITY_KEYWORDS):
                utility_bills.append(bill)

        if not utility_bills:
            return ResearchProviderResult(
                provider_id=self.provider_id,
                skipped=True,
                error="No utility/internet/mobile bills found",
            )

        total = sum(float(b.get("amount", 0)) for b in utility_bills)
        offers = [
            ResearchOffer(
                provider="utility_scanner",
                product_type="utility",
                estimated_savings=0,
                eligibility_notes=f"{len(utility_bills)} utility-type bills totaling ${total:.0f}/period",
                source_url="local://bills",
                confidence=0.7,
                raw_source="bill_classification",
                math={
                    "bills": [{"name": b.get("name"), "amount": b.get("amount")} for b in utility_bills],
                    "total": total,
                },
                warnings=[
                    "Compare providers manually or via state utility choice programs",
                    "Check assistance programs if bills are hardship-level",
                ],
            )
        ]
        return ResearchProviderResult(provider_id=self.provider_id, offers=offers)
