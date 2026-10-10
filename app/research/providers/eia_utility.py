"""EIA Open Data — regional electricity price benchmark (cents/kWh)."""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.research.market_datum import MarketDatum
from app.research.providers.cache import read_cache, write_cache

EIA_BASE = "https://api.eia.gov/v2/electricity/retail-sales/data/"


def eia_api_key() -> str:
    return os.getenv("EIA_API_KEY", "").strip()


def _state_from_context(context: dict[str, Any] | None) -> str:
    if not context:
        return "US"
    borrower = context.get("borrower") or {}
    state = (borrower.get("state") or "").strip().upper()[:2]
    return state if len(state) == 2 else "US"


def fetch_eia_electricity_price(
    state_id: str = "US",
    *,
    api_key: str | None = None,
    use_cache: bool = True,
) -> MarketDatum | None:
    key = (api_key or eia_api_key()).strip()
    if not key:
        return None

    cache_id = f"eia_electricity_{state_id}"
    if use_cache:
        cached = read_cache(cache_id)
        if cached and cached.get("value") is not None:
            return MarketDatum(
                series_id=f"EIA.ELEC.{state_id}",
                value=float(cached["value"]),
                observation_date=cached.get("period", ""),
                source_url="https://www.eia.gov/electricity/data.php",
                source_label=f"EIA retail electricity ({state_id})",
                confidence=0.82,
                unit="cents_per_kwh",
            )

    params: dict[str, Any] = {
        "api_key": key,
        "frequency": "monthly",
        "data[0]": "price",
        "facets[sectorid][]": "RES",
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "length": 1,
    }
    if state_id != "US":
        params["facets[stateid][]"] = state_id
    else:
        params["facets[stateid][]"] = "US"

    try:
        resp = httpx.get(EIA_BASE, params=params, timeout=20.0)
        resp.raise_for_status()
        rows = resp.json().get("response", {}).get("data") or []
        if not rows:
            return None
        row = rows[0]
        price = float(row.get("price", 0))
        period = str(row.get("period", ""))
        write_cache(cache_id, {"value": price, "period": period})
        return MarketDatum(
            series_id=f"EIA.ELEC.{state_id}",
            value=price,
            observation_date=period,
            source_url="https://www.eia.gov/opendata/",
            source_label=f"EIA residential electricity ({state_id})",
            confidence=0.85,
            unit="cents_per_kwh",
            warnings=["Regional benchmark — compare to your utility bill, not a quote"],
        )
    except Exception:
        return None


def fetch_eia_utility_payload(context: dict[str, Any] | None = None) -> dict[str, Any]:
    state = _state_from_context(context)
    key = eia_api_key()
    if not key:
        return {
            "source_url": "https://www.eia.gov/opendata/",
            "source_label": "EIA (unavailable — set EIA_API_KEY)",
            "error": "EIA_API_KEY not set",
            "state": state,
        }

    datum = fetch_eia_electricity_price(state, api_key=key)
    if datum is None and state != "US":
        datum = fetch_eia_electricity_price("US", api_key=key)
        state = "US"

    if datum is None:
        return {
            "source_url": "https://www.eia.gov/opendata/",
            "source_label": "EIA",
            "error": "no EIA electricity data",
            "state": state,
        }

    return {
        "source_url": datum.source_url,
        "source_label": datum.source_label,
        "as_of": datum.observation_date,
        "observation_date": datum.observation_date,
        "provider": "EIA",
        "state": state,
        "electricity_cents_per_kwh": datum.value,
        "primary_value": datum.value,
        "series_id": datum.series_id,
        "confidence": datum.confidence,
        "warnings": list(datum.warnings),
    }


def doctor_check_eia() -> dict[str, Any]:
    key = eia_api_key()
    if not key:
        return {"ok": False, "error": "EIA_API_KEY not set"}
    d = fetch_eia_electricity_price("US", api_key=key, use_cache=False)
    return {
        "ok": d is not None,
        "error": None if d else "EIA fetch failed",
        "sample": d.to_dict() if d else None,
    }
