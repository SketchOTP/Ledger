"""FRED series fetcher — shared client for all St. Louis Fed market benchmarks."""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.research.market_datum import MarketDatum, fred_series_url
from app.research.providers.cache import read_cache, write_cache

FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"

# Phase A catalog: series_id -> metadata
FRED_SERIES_CATALOG: dict[str, dict[str, Any]] = {
    "MORTGAGE30US": {
        "label": "30-Year Fixed Rate Mortgage Average",
        "unit": "percent",
        "confidence": 0.9,
        "ingestion_source": "mortgage_rates",
        "payload_key": "rate_30yr",
    },
    "MORTGAGE15US": {
        "label": "15-Year Fixed Rate Mortgage Average",
        "unit": "percent",
        "confidence": 0.9,
        "ingestion_source": "mortgage_rates",
        "payload_key": "rate_15yr",
    },
    "DPRIME": {
        "label": "Bank Prime Loan Rate",
        "unit": "percent",
        "confidence": 0.88,
        "ingestion_source": "fred_macro_rates",
        "payload_key": "prime_rate",
    },
    "FEDFUNDS": {
        "label": "Federal Funds Effective Rate",
        "unit": "percent",
        "confidence": 0.88,
        "ingestion_source": "fred_macro_rates",
        "payload_key": "fed_funds_rate",
    },
    "DGS10": {
        "label": "10-Year Treasury Constant Maturity Rate",
        "unit": "percent",
        "confidence": 0.88,
        "ingestion_source": "fred_macro_rates",
        "payload_key": "treasury_10y",
    },
    "TERMCBCCINT": {
        "label": "Commercial Bank Credit Card Interest Rate",
        "unit": "percent",
        "confidence": 0.82,
        "ingestion_source": "credit_card_benchmark",
        "payload_key": "benchmark_apr_percent",
    },
    "TERMCBPER24NS": {
        "label": "24-Month Personal Loan Rate (G.19)",
        "unit": "percent",
        "confidence": 0.82,
        "ingestion_source": "personal_loan_rates",
        "payload_key": "typical_apr_percent",
    },
    "TERMCBAUTO48NS": {
        "label": "48-Month New Auto Loan Rate",
        "unit": "percent",
        "confidence": 0.82,
        "ingestion_source": "auto_loan_rates",
        "payload_key": "typical_apr_percent",
    },
    "CUSR0000SAF11": {
        "label": "CPI Food at Home (SA)",
        "unit": "index",
        "confidence": 0.8,
        "ingestion_source": "food_cpi_trend",
        "payload_key": "index_value",
    },
    "CUSR0000SEHF01": {
        "label": "CPI Electricity (SA)",
        "unit": "index",
        "confidence": 0.8,
        "ingestion_source": "utility_cpi_trend",
        "payload_key": "index_value",
    },
}

INGESTION_FRED_GROUPS: dict[str, list[str]] = {
    "mortgage_rates": ["MORTGAGE30US", "MORTGAGE15US"],
    "fred_macro_rates": ["DPRIME", "FEDFUNDS", "DGS10"],
    "credit_card_benchmark": ["TERMCBCCINT"],
    "personal_loan_rates": ["TERMCBPER24NS"],
    "auto_loan_rates": ["TERMCBAUTO48NS"],
    "food_cpi_trend": ["CUSR0000SAF11"],
    "utility_cpi_trend": ["CUSR0000SEHF01"],
}


def fred_api_key() -> str:
    return os.getenv("FRED_API_KEY", "").strip()


def fetch_fred_observation(
    series_id: str,
    *,
    api_key: str | None = None,
    use_cache: bool = True,
) -> MarketDatum | None:
    """Fetch latest observation for one FRED series."""
    key = (api_key or fred_api_key()).strip()
    if not key:
        return None

    meta = FRED_SERIES_CATALOG.get(series_id, {})
    cache_id = f"fred_{series_id}"
    if use_cache:
        cached = read_cache(cache_id)
        if cached and cached.get("value") is not None:
            return MarketDatum(
                series_id=series_id,
                value=float(cached["value"]),
                observation_date=cached.get("observation_date", ""),
                source_url=fred_series_url(series_id),
                source_label=meta.get("label", series_id),
                confidence=float(meta.get("confidence", 0.85)),
                fetched_at=cached.get("fetched_at", ""),
                unit=meta.get("unit", "percent"),
            )

    try:
        resp = httpx.get(
            FRED_OBSERVATIONS_URL,
            params={
                "series_id": series_id,
                "api_key": key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 1,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        obs = resp.json().get("observations", [])
        if not obs or obs[0].get("value") in (".", None, ""):
            return None
        value = float(obs[0]["value"])
        obs_date = obs[0].get("date", "")
        write_cache(
            cache_id,
            {"value": value, "observation_date": obs_date},
        )
        return MarketDatum(
            series_id=series_id,
            value=value,
            observation_date=obs_date,
            source_url=fred_series_url(series_id),
            source_label=meta.get("label", series_id),
            confidence=float(meta.get("confidence", 0.85)),
            unit=meta.get("unit", "percent"),
        )
    except Exception:
        return None


def fetch_fred_group(source_type: str) -> dict[str, Any]:
    """
    Fetch all FRED series for an ingestion source_type.
    Returns raw payload for normalize_finding with source_url and series map.
    """
    series_ids = INGESTION_FRED_GROUPS.get(source_type, [])
    if not series_ids:
        return {"source_url": "", "error": f"no FRED group for {source_type}"}

    key = fred_api_key()
    if not key:
        primary = series_ids[0]
        return {
            "source_url": fred_series_url(primary),
            "source_label": "FRED (unavailable — set FRED_API_KEY)",
            "error": "FRED_API_KEY not set",
            "series": {},
        }

    series_data: dict[str, Any] = {}
    observation_dates: list[str] = []
    for sid in series_ids:
        datum = fetch_fred_observation(sid, api_key=key)
        if datum is None:
            continue
        meta = FRED_SERIES_CATALOG[sid]
        payload_key = meta["payload_key"]
        series_data[payload_key] = datum.value
        series_data[f"{payload_key}_series_id"] = sid
        series_data[f"{payload_key}_observation_date"] = datum.observation_date
        observation_dates.append(datum.observation_date)

    if not series_data:
        return {
            "source_url": fred_series_url(series_ids[0]),
            "source_label": "FRED",
            "error": "no valid observations",
            "series": {},
        }

    as_of = max(observation_dates) if observation_dates else None
    primary_sid = series_ids[0]
    primary_meta = FRED_SERIES_CATALOG[primary_sid]
    primary_key = primary_meta["payload_key"]

    out: dict[str, Any] = {
        "source_url": fred_series_url(primary_sid),
        "source_label": f"FRED {primary_sid}",
        "as_of": as_of,
        "observation_date": as_of,
        "provider": "FRED",
        "series": series_data,
        primary_key: series_data.get(primary_key),
    }
    out.update(series_data)

    if source_type == "mortgage_rates":
        out["rate_percent"] = series_data.get("rate_30yr")
        out["rate_15yr"] = series_data.get("rate_15yr")
    elif source_type == "credit_card_benchmark":
        out["typical_promo_apr_percent"] = series_data.get("benchmark_apr_percent")
        out["benchmark_apr_percent"] = series_data.get("benchmark_apr_percent")
    elif source_type in ("personal_loan_rates", "auto_loan_rates"):
        out["typical_apr_percent"] = series_data.get("typical_apr_percent")

    return out


def doctor_check_fred() -> dict[str, Any]:
    """Health check for FRED connectivity (used by CLI doctor)."""
    key = fred_api_key()
    if not key:
        return {"ok": False, "error": "FRED_API_KEY not set", "series_tested": []}
    tested: list[dict[str, Any]] = []
    for sid in ("MORTGAGE30US", "MORTGAGE15US", "TERMCBPER24NS"):
        d = fetch_fred_observation(sid, api_key=key, use_cache=False)
        tested.append(
            {
                "series_id": sid,
                "ok": d is not None,
                "value": d.value if d else None,
                "observation_date": d.observation_date if d else None,
            }
        )
    ok = all(t["ok"] for t in tested)
    return {"ok": ok, "error": None if ok else "one or more series failed", "series_tested": tested}
