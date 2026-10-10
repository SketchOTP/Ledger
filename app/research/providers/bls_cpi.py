"""BLS CPI — food at home and electricity trend (public API, optional registration key)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import httpx

from app.research.market_datum import MarketDatum
from app.research.providers.cache import read_cache, write_cache

BLS_V2_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BLS_V1_URL = "https://api.bls.gov/publicAPI/v1/timeseries/data"

# Not seasonally adjusted — BLS native IDs (FRED uses CUSR* SA variants)
BLS_SERIES = {
    "food_at_home": {
        "series_id": "CUUR0000SAF111",
        "label": "CPI Food at Home (BLS)",
        "unit": "index",
        "payload_key": "food_index_value",
    },
    "electricity": {
        "series_id": "CUUR0000SEHF01",
        "label": "CPI Electricity (BLS)",
        "unit": "index",
        "payload_key": "electricity_index_value",
    },
}


def bls_api_key() -> str:
    return os.getenv("BLS_API_KEY", "").strip()


def _current_year_range() -> tuple[str, str]:
    y = datetime.now(UTC).year
    return str(y - 1), str(y)


def _parse_bls_response(data: dict[str, Any], series_id: str) -> tuple[float | None, str | None]:
    results = data.get("Results") or data.get("results") or {}
    series_list = results.get("series") or []
    if not series_list:
        return None, None
    for series in series_list:
        if series.get("seriesID") != series_id and series.get("seriesId") != series_id:
            continue
        points = series.get("data") or []
        for point in points:
            val = point.get("value")
            if val in (None, "", "-"):
                continue
            try:
                return float(val), point.get("periodName", "") + " " + point.get("year", "")
            except (TypeError, ValueError):
                continue
    return None, None


def fetch_bls_series(series_id: str, *, use_cache: bool = True) -> MarketDatum | None:
    cache_id = f"bls_{series_id}"
    if use_cache:
        cached = read_cache(cache_id)
        if cached and cached.get("value") is not None:
            meta = next((m for m in BLS_SERIES.values() if m["series_id"] == series_id), {})
            return MarketDatum(
                series_id=series_id,
                value=float(cached["value"]),
                observation_date=cached.get("observation_date", ""),
                source_url=f"https://data.bls.gov/timeseries/{series_id}",
                source_label=meta.get("label", series_id),
                confidence=0.78,
                unit="index",
            )

    start_year, end_year = _current_year_range()
    key = bls_api_key()
    body: dict[str, Any] = {
        "seriesid": [series_id],
        "startyear": start_year,
        "endyear": end_year,
    }
    if key:
        body["registrationkey"] = key

    try:
        resp = httpx.post(BLS_V2_URL, json=body, timeout=20.0)
        if resp.status_code == 405 or resp.status_code == 404:
            resp = httpx.get(
                f"{BLS_V1_URL}/{series_id}",
                params={"startyear": start_year, "endyear": end_year},
                timeout=20.0,
            )
        resp.raise_for_status()
        value, period = _parse_bls_response(resp.json(), series_id)
        if value is None:
            return None
        obs_date = period or datetime.now(UTC).date().isoformat()
        write_cache(cache_id, {"value": value, "observation_date": obs_date})
        meta = next((m for m in BLS_SERIES.values() if m["series_id"] == series_id), {})
        return MarketDatum(
            series_id=series_id,
            value=value,
            observation_date=obs_date,
            source_url=f"https://data.bls.gov/timeseries/{series_id}",
            source_label=meta.get("label", series_id),
            confidence=0.8 if key else 0.75,
            unit="index",
            warnings=[] if key else ["BLS v2 without registration key — rate limits apply"],
        )
    except Exception:
        return None


def fetch_bls_cpi_payload() -> dict[str, Any]:
    """Raw payload for bls_cpi_trend ingestion source."""
    series_data: dict[str, Any] = {}
    dates: list[str] = []
    yoy_changes: list[float] = []

    for key, meta in BLS_SERIES.items():
        datum = fetch_bls_series(meta["series_id"])
        if datum is None:
            continue
        pk = meta["payload_key"]
        series_data[pk] = datum.value
        series_data[f"{pk}_series_id"] = meta["series_id"]
        series_data[f"{pk}_observation_date"] = datum.observation_date
        dates.append(datum.observation_date)
        # YoY: fetch prior year same month if we have two points — simplified via second call
        prior = _fetch_prior_year_value(meta["series_id"])
        if prior and prior > 0:
            yoy = round((datum.value - prior) / prior * 100, 2)
            series_data[f"{key}_yoy_percent"] = yoy
            yoy_changes.append(yoy)

    if not series_data:
        return {
            "source_url": "https://www.bls.gov/cpi/",
            "source_label": "BLS CPI (unavailable)",
            "error": "BLS CPI fetch failed",
        }

    as_of = max(dates) if dates else datetime.now(UTC).date().isoformat()
    primary = series_data.get("food_index_value") or series_data.get("electricity_index_value")
    return {
        "source_url": "https://www.bls.gov/cpi/",
        "source_label": "U.S. Bureau of Labor Statistics CPI",
        "as_of": as_of,
        "observation_date": as_of,
        "provider": "BLS",
        "index_value": primary,
        "food_index_value": series_data.get("food_index_value"),
        "electricity_index_value": series_data.get("electricity_index_value"),
        "food_yoy_percent": series_data.get("food_at_home_yoy_percent"),
        "electricity_yoy_percent": series_data.get("electricity_yoy_percent"),
        "yoy_change_percent": round(sum(yoy_changes) / len(yoy_changes), 2) if yoy_changes else None,
        "series": series_data,
        "warnings": ["Index trend context only — not shelf-level grocery prices"],
    }


def _fetch_prior_year_value(series_id: str) -> float | None:
    y = datetime.now(UTC).year - 2
    body: dict[str, Any] = {
        "seriesid": [series_id],
        "startyear": str(y),
        "endyear": str(y + 1),
    }
    key = bls_api_key()
    if key:
        body["registrationkey"] = key
    try:
        resp = httpx.post(BLS_V2_URL, json=body, timeout=15.0)
        resp.raise_for_status()
        val, _ = _parse_bls_response(resp.json(), series_id)
        return val
    except Exception:
        return None


def doctor_check_bls() -> dict[str, Any]:
    food = fetch_bls_series(BLS_SERIES["food_at_home"]["series_id"], use_cache=False)
    elec = fetch_bls_series(BLS_SERIES["electricity"]["series_id"], use_cache=False)
    ok = food is not None or elec is not None
    return {
        "ok": ok,
        "error": None if ok else "BLS CPI fetch failed",
        "food_at_home": food.to_dict() if food else None,
        "electricity": elec.to_dict() if elec else None,
        "api_key_set": bool(bls_api_key()),
    }
