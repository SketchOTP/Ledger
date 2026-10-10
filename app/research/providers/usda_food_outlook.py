"""USDA ERS Food Price Outlook — monthly food inflation forecast trend (context only)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from app.research.providers.cache import read_cache, write_cache

# ERS publishes outlook tables; this endpoint may change — fixture-friendly.
USDA_OUTLOOK_JSON = (
    "https://www.ers.usda.gov/webdocs/DataFiles/52000/"
    "FoodPriceOutlook.json"
)
USDA_SUMMARY_URL = "https://www.ers.usda.gov/data-products/food-price-outlook/"


def fetch_usda_food_outlook_payload() -> dict[str, Any]:
    """
    Fetch USDA food price outlook trend.
    Uses optional USDA_ERS_API_KEY for api.ers.usda.gov when set;
    otherwise attempts public JSON or returns structured skip for manual curation.
    """
    cache_id = "usda_food_outlook"
    cached = read_cache(cache_id, ttl_hours=168)
    if cached and cached.get("forecast_yoy_percent") is not None:
        return {**cached, "from_cache": True}

    api_key = os.getenv("USDA_ERS_API_KEY", "").strip()
    if api_key:
        payload = _fetch_ers_api(api_key)
        if payload:
            write_cache(cache_id, payload)
            return payload

    payload = _fetch_public_json()
    if payload:
        write_cache(cache_id, payload)
        return payload

    return {
        "source_url": USDA_SUMMARY_URL,
        "source_label": "USDA ERS Food Price Outlook",
        "error": "USDA outlook unavailable — update via Research Admin or fixture",
        "warnings": [
            "No live USDA feed — use approved curated usda_food_outlook section",
            "Trend context only; not store-level prices",
        ],
    }


def _fetch_ers_api(api_key: str) -> dict[str, Any] | None:
    try:
        resp = httpx.get(
            "https://api.ers.usda.gov/data/arms/survey",
            params={"api_key": api_key, "format": "json"},
            timeout=15.0,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not data:
            return None
        return {
            "source_url": USDA_SUMMARY_URL,
            "source_label": "USDA ERS API",
            "as_of": datetime.now(UTC).date().isoformat(),
            "provider": "USDA",
            "forecast_yoy_percent": None,
            "raw": data,
            "warnings": ["Verify forecast fields against ERS Food Price Outlook release"],
        }
    except Exception:
        return None


def _fetch_public_json() -> dict[str, Any] | None:
    try:
        resp = httpx.get(USDA_OUTLOOK_JSON, timeout=15.0, follow_redirects=True)
        if resp.status_code != 200:
            return None
        data = resp.json()
        forecast = _extract_forecast_yoy(data)
        return {
            "source_url": USDA_SUMMARY_URL,
            "source_label": "USDA ERS Food Price Outlook",
            "as_of": data.get("release_date") or datetime.now(UTC).date().isoformat(),
            "observation_date": data.get("release_date") or datetime.now(UTC).date().isoformat(),
            "provider": "USDA",
            "forecast_yoy_percent": forecast,
            "food_at_home_forecast_yoy": forecast,
            "payload": data,
            "warnings": ["USDA forecast — context only, not shelf prices"],
        }
    except (httpx.HTTPError, json.JSONDecodeError, OSError):
        return None


def _extract_forecast_yoy(data: dict[str, Any]) -> float | None:
    """Best-effort parse of ERS JSON shape (varies by release)."""
    for key in ("food_at_home", "all_food", "forecast"):
        block = data.get(key)
        if isinstance(block, dict):
            for field in ("yoy_change", "yoy_percent", "change_percent", "forecast"):
                v = block.get(field)
                if v is not None:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        pass
    if "forecast_yoy_percent" in data:
        try:
            return float(data["forecast_yoy_percent"])
        except (TypeError, ValueError):
            pass
    return None


def doctor_check_usda() -> dict[str, Any]:
    payload = fetch_usda_food_outlook_payload()
    ok = payload.get("forecast_yoy_percent") is not None and not payload.get("error")
    return {
        "ok": ok,
        "error": payload.get("error"),
        "forecast_yoy_percent": payload.get("forecast_yoy_percent"),
        "source_url": payload.get("source_url"),
    }
