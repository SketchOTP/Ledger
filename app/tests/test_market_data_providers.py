"""Market data provider tests — HTTP mocked; no live API required."""

from unittest.mock import MagicMock, patch

import pytest

from app.research.market_datum import MarketDatum
from app.research.providers.fred_series import (
    FRED_SERIES_CATALOG,
    doctor_check_fred,
    fetch_fred_group,
    fetch_fred_observation,
)


def _fred_obs_response(value: str, date: str = "2026-05-20") -> dict:
    return {"observations": [{"date": date, "value": value}]}


@pytest.fixture
def fred_key(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "test-fred-key")


@pytest.fixture(autouse=True)
def _clear_fred_disk_cache():
    """Live ingest writes data/research_cache — keep unit tests on mocked FRED values."""
    from app.research.providers.cache import CACHE_DIR

    if CACHE_DIR.is_dir():
        for path in CACHE_DIR.glob("fred_*.json"):
            path.unlink(missing_ok=True)
    yield


@pytest.fixture
def mock_fred_http():
    def _handler(url, params=None, **kwargs):
        sid = (params or {}).get("series_id", "")
        values = {
            "MORTGAGE30US": "6.51",
            "MORTGAGE15US": "5.89",
            "DPRIME": "8.50",
            "FEDFUNDS": "5.33",
            "DGS10": "4.25",
            "TERMCBCCINT": "21.47",
            "TERMCBPER24NS": "11.23",
            "TERMCBAUTO48NS": "7.88",
            "CUSR0000SAF11": "312.5",
            "CUSR0000SEHF01": "285.1",
        }
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = _fred_obs_response(values.get(sid, "1.0"))
        return resp

    with (
        patch("app.research.providers.fred_series.read_cache", return_value=None),
        patch("app.research.providers.fred_series.httpx.get", side_effect=_handler),
    ):
        yield


def test_market_datum_fields():
    d = MarketDatum(
        series_id="MORTGAGE30US",
        value=6.51,
        observation_date="2026-05-20",
        source_url="https://fred.stlouisfed.org/series/MORTGAGE30US",
        source_label="30yr mortgage",
        confidence=0.9,
    )
    out = d.to_dict()
    assert out["series_id"] == "MORTGAGE30US"
    assert out["value"] == 6.51
    assert out["source_url"].startswith("https://")


def test_fred_fetch_observation(fred_key, mock_fred_http):
    d = fetch_fred_observation("MORTGAGE30US", use_cache=False)
    assert d is not None
    assert d.value == 6.51
    assert d.series_id == "MORTGAGE30US"


def test_fred_mortgage_group_includes_15yr(fred_key, mock_fred_http):
    raw = fetch_fred_group("mortgage_rates")
    assert raw.get("rate_percent") == 6.51
    assert raw.get("rate_15yr") == 5.89
    assert raw["source_url"]


def test_fred_macro_group(fred_key, mock_fred_http):
    raw = fetch_fred_group("fred_macro_rates")
    assert raw.get("prime_rate") == 8.50
    assert raw.get("fed_funds_rate") == 5.33
    assert raw.get("treasury_10y") == 4.25


def test_fred_personal_and_auto_loan(fred_key, mock_fred_http):
    pl = fetch_fred_group("personal_loan_rates")
    assert pl.get("typical_apr_percent") == 11.23
    auto = fetch_fred_group("auto_loan_rates")
    assert auto.get("typical_apr_percent") == 7.88


def test_fred_credit_card_benchmark(fred_key, mock_fred_http):
    raw = fetch_fred_group("credit_card_benchmark")
    assert raw.get("benchmark_apr_percent") == 21.47


def test_fred_catalog_covers_phase_a_series():
    expected = {
        "MORTGAGE30US",
        "MORTGAGE15US",
        "DPRIME",
        "FEDFUNDS",
        "DGS10",
        "TERMCBCCINT",
        "TERMCBPER24NS",
        "TERMCBAUTO48NS",
        "CUSR0000SAF11",
        "CUSR0000SEHF01",
    }
    assert expected <= set(FRED_SERIES_CATALOG.keys())


def test_fred_doctor_without_key(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    result = doctor_check_fred()
    assert result["ok"] is False


def test_bls_cpi_fetch(monkeypatch):
    monkeypatch.setenv("BLS_API_KEY", "test-bls")

    def fake_post(url, json=None, **kwargs):
        sid = (json or {}).get("seriesid", ["CUUR0000SAF111"])[0]
        val = "310.2" if "SAF" in sid else "304.0"
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "Results": {
                "series": [
                    {
                        "seriesID": sid,
                        "data": [{"year": "2026", "periodName": "M04", "value": val}],
                    }
                ]
            }
        }
        return resp

    with (
        patch("app.research.providers.bls_cpi.read_cache", return_value=None),
        patch("app.research.providers.bls_cpi._fetch_prior_year_value", return_value=None),
        patch("app.research.providers.bls_cpi.httpx.post", side_effect=fake_post),
    ):
        from app.research.providers.bls_cpi import fetch_bls_cpi_payload

        raw = fetch_bls_cpi_payload()
    assert raw.get("food_index_value") == 310.2
    assert raw["source_url"]


def test_eia_utility_fetch(monkeypatch):
    monkeypatch.setenv("EIA_API_KEY", "test-eia")

    def fake_get(url, params=None, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "response": {"data": [{"price": "14.52", "period": "2026-03"}]}
        }
        return resp

    with patch("app.research.providers.eia_utility.httpx.get", side_effect=fake_get):
        from app.research.providers.eia_utility import fetch_eia_utility_payload

        raw = fetch_eia_utility_payload({"borrower": {"state": "CA"}})
    assert raw.get("electricity_cents_per_kwh") == 14.52
    assert raw.get("state") == "CA"
