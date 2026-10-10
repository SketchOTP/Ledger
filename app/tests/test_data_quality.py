"""Canonical data-quality and provenance tests."""

import json
from datetime import date, timedelta

from sqlmodel import select

from app.core.data_quality import assess_data_quality
from app.db.models import (
    BorrowerProfile,
    CreditCard,
    EventLogEntry,
    IncomeSource,
)
from app.events.bus import event_bus
from app.events.types import EventType
from app.research.ingestion.types import STALE_AFTER_DAYS


def test_missing_apr_flags_credit_card(session):
    session.add(
        CreditCard(
            issuer="Test Bank",
            card_name="Rewards",
            balance=2000,
            credit_limit=5000,
            apr=0,
            minimum_payment=50,
        )
    )
    session.commit()

    report = assess_data_quality(session, emit_events=False)
    cc = next(d for d in report["domains"] if d["domain"] == "credit_cards")
    assert cc["status"] == "unsafe"
    assert any(i["check"] == "missing_apr" for i in cc["issues"])
    assert any(i["check"] == "missing_apr" for i in report["blocking_issues"])


def test_stale_research_rate_flags_source(session, monkeypatch, tmp_path):
    curated = tmp_path / "curated_rates.json"
    old_date = (date.today() - timedelta(days=STALE_AFTER_DAYS + 10)).isoformat()
    curated.write_text(
        json.dumps(
            {
                "mortgage_rates": {
                    "rate_30yr": 6.5,
                    "as_of": old_date,
                    "source_url": "https://example.com/mortgage",
                    "source_label": "test",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.core.data_quality._load_curated", lambda: json.loads(curated.read_text()))
    monkeypatch.setattr("app.research.providers.curated_rates._load_curated", lambda: json.loads(curated.read_text()))

    report = assess_data_quality(session, emit_events=False)
    research = next(d for d in report["domains"] if d["domain"] == "research_rates")
    assert any(i["check"] == "stale_values" for i in research["issues"])
    assert any("stale" in w["message"].lower() for w in report["warnings"])


def test_data_quality_emits_events(session, sample_data):
    event_bus.set_session(session)
    assess_data_quality(session, emit_events=True)
    types = {e.event_type for e in session.exec(select(EventLogEntry)).all()}
    assert EventType.DATA_QUALITY_CHECKED.value in types


def test_income_sources_clear_borrower_blocking(session):
    session.add(BorrowerProfile(checking_balance=100, credit_score=720))
    session.add(
        IncomeSource(
            source_name="Job",
            amount=2000,
            pay_frequency="biweekly",
        )
    )
    session.commit()

    report = assess_data_quality(session, emit_events=False)
    bp = next(d for d in report["domains"] if d["domain"] == "borrower_profile")
    assert not any(
        i["check"] == "missing_required_fields" and "missing income" in i["message"].lower()
        for i in bp["issues"]
    )
    assert bp["status"] != "unsafe"


def test_curated_mortgage_rate_unblocks_research(session, monkeypatch, tmp_path):
    curated = tmp_path / "curated_rates.json"
    curated.write_text(
        json.dumps(
            {
                "mortgage_rates": {
                    "rate_30yr": 6.5,
                    "as_of": date.today().isoformat(),
                    "source_url": "https://example.com/mortgage",
                    "source_label": "test",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.core.data_quality._load_curated", lambda: json.loads(curated.read_text()))
    report = assess_data_quality(session, emit_events=False)
    assert not any(i["check"] == "missing_mortgage_market_rate" for i in report["blocking_issues"])
