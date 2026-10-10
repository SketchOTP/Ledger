"""Canonical market data point with required provenance fields."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class MarketDatum:
    series_id: str
    value: float
    observation_date: str
    source_url: str
    source_label: str
    confidence: float
    fetched_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"))
    unit: str = "percent"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "series_id": self.series_id,
            "value": self.value,
            "observation_date": self.observation_date,
            "source_url": self.source_url,
            "source_label": self.source_label,
            "confidence": self.confidence,
            "fetched_at": self.fetched_at,
            "unit": self.unit,
            "warnings": list(self.warnings),
        }


def fred_series_url(series_id: str) -> str:
    return f"https://fred.stlouisfed.org/series/{series_id}"
