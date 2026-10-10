"""Per-user JSON stores for behavioral finance profiles (beside household DB)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.db.user_db import user_db_path

LIFE_GOALS_KEYS = (
    "positive_emotion",
    "engagement",
    "relationships",
    "meaning",
    "accomplishment",
)

RISK_CHECKLIST_KEYS = (
    "emergency_fund",
    "life_insurance",
    "disability_insurance",
    "will",
    "beneficiaries_reviewed",
    "estate_plan",
)

RISK_CHECKLIST_VALUES = frozenset(
    {"complete", "partial", "missing", "unknown", "not_applicable"}
)

HUMAN_CAPITAL_TYPES = frozenset(
    {
        "course",
        "certification",
        "tool",
        "business_project",
        "book",
        "conference",
        "other",
    }
)

LIFE_GOALS_DEFAULT: dict[str, list[str]] = {k: [] for k in LIFE_GOALS_KEYS}

RISK_CHECKLIST_DEFAULT: dict[str, Any] = {
    "emergency_fund": "unknown",
    "life_insurance": "unknown",
    "disability_insurance": "unknown",
    "will": "unknown",
    "beneficiaries_reviewed": "unknown",
    "estate_plan": "unknown",
    "last_reviewed": None,
}

# These files are user-authored persistent state.  Keep the inventory in one
# place so backup, restore, reset, and future V2 adapters cannot silently
# diverge on which behavioral records are preserved.
BEHAVIORAL_FILENAMES = ("life_goals.json", "risk_checklist.json", "human_capital.json")


def _user_data_dir(user_id: str) -> Path:
    path = user_db_path(user_id).parent
    path.mkdir(parents=True, exist_ok=True)
    return path


def behavioral_file_paths(user_id: str) -> dict[str, Path]:
    """Return the explicit behavioral sidecar paths for one household."""
    root = user_db_path(user_id).parent
    return {name: root / name for name in BEHAVIORAL_FILENAMES}


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if data is not None else default
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def life_goals_path(user_id: str) -> Path:
    return behavioral_file_paths(user_id)["life_goals.json"]


def risk_checklist_path(user_id: str) -> Path:
    return behavioral_file_paths(user_id)["risk_checklist.json"]


def human_capital_path(user_id: str) -> Path:
    return behavioral_file_paths(user_id)["human_capital.json"]


def _normalize_string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def load_life_goals(user_id: str) -> dict[str, list[str]]:
    raw = _read_json(life_goals_path(user_id), {})
    if not isinstance(raw, dict):
        return dict(LIFE_GOALS_DEFAULT)
    return {k: _normalize_string_list(raw.get(k)) for k in LIFE_GOALS_KEYS}


def save_life_goals(user_id: str, data: dict[str, Any]) -> dict[str, list[str]]:
    cleaned = {k: _normalize_string_list(data.get(k)) for k in LIFE_GOALS_KEYS}
    _write_json(life_goals_path(user_id), cleaned)
    return cleaned


def life_goals_configured(goals: dict[str, list[str]]) -> bool:
    return any(goals.get(k) for k in LIFE_GOALS_KEYS)


def load_risk_checklist(user_id: str) -> dict[str, Any]:
    raw = _read_json(risk_checklist_path(user_id), {})
    out = dict(RISK_CHECKLIST_DEFAULT)
    if isinstance(raw, dict):
        for key in RISK_CHECKLIST_KEYS:
            val = raw.get(key)
            if val in RISK_CHECKLIST_VALUES:
                out[key] = val
        reviewed = raw.get("last_reviewed")
        if reviewed is None or isinstance(reviewed, str):
            out["last_reviewed"] = reviewed
    return out


def save_risk_checklist(user_id: str, data: dict[str, Any]) -> dict[str, Any]:
    out = load_risk_checklist(user_id)
    for key in RISK_CHECKLIST_KEYS:
        val = data.get(key)
        if val is not None:
            if val not in RISK_CHECKLIST_VALUES:
                raise ValueError(f"Invalid value for {key}: {val}")
            out[key] = val
    reviewed = data.get("last_reviewed")
    if reviewed is not None:
        if reviewed == "":
            out["last_reviewed"] = None
        elif isinstance(reviewed, str):
            out["last_reviewed"] = reviewed
    _write_json(risk_checklist_path(user_id), out)
    return out


def load_human_capital_records(user_id: str) -> list[dict[str, Any]]:
    raw = _read_json(human_capital_path(user_id), [])
    if not isinstance(raw, list):
        return []
    records: list[dict[str, Any]] = []
    for row in raw:
        if isinstance(row, dict) and row.get("id"):
            records.append(row)
    return records


def save_human_capital_records(user_id: str, records: list[dict[str, Any]]) -> None:
    _write_json(human_capital_path(user_id), records)


def _normalize_human_capital_record(data: dict[str, Any], *, record_id: str | None = None) -> dict[str, Any]:
    rec_type = str(data.get("type") or "other").strip().lower()
    if rec_type not in HUMAN_CAPITAL_TYPES:
        raise ValueError(f"Invalid type: {rec_type}")
    date_str = str(data.get("date") or date.today().isoformat())
    try:
        date.fromisoformat(date_str)
    except ValueError as exc:
        raise ValueError("date must be YYYY-MM-DD") from exc
    cost = float(data.get("cost") or 0)
    impact = float(data.get("actual_income_impact") or 0)
    return {
        "id": record_id or str(data.get("id") or uuid.uuid4()),
        "date": date_str,
        "type": rec_type,
        "description": str(data.get("description") or "").strip(),
        "cost": round(max(0.0, cost), 2),
        "expected_return": str(data.get("expected_return") or "").strip(),
        "actual_income_impact": round(impact, 2),
        "notes": str(data.get("notes") or "").strip(),
        "updated_at": datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds"),
    }


def create_human_capital_record(user_id: str, data: dict[str, Any]) -> dict[str, Any]:
    records = load_human_capital_records(user_id)
    record = _normalize_human_capital_record(data)
    records.append(record)
    save_human_capital_records(user_id, records)
    return record


def update_human_capital_record(user_id: str, record_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    records = load_human_capital_records(user_id)
    for i, row in enumerate(records):
        if row.get("id") == record_id:
            merged = {**row, **data, "id": record_id}
            records[i] = _normalize_human_capital_record(merged, record_id=record_id)
            save_human_capital_records(user_id, records)
            return records[i]
    return None


def delete_human_capital_record(user_id: str, record_id: str) -> bool:
    records = load_human_capital_records(user_id)
    new_rows = [r for r in records if r.get("id") != record_id]
    if len(new_rows) == len(records):
        return False
    save_human_capital_records(user_id, new_rows)
    return True


def human_capital_summary(user_id: str) -> dict[str, Any]:
    records = load_human_capital_records(user_id)
    year = date.today().year
    year_records = [r for r in records if str(r.get("date", "")).startswith(f"{year}-")]
    total_invested = round(sum(float(r.get("cost") or 0) for r in year_records), 2)
    total_impact = round(
        sum(float(r.get("actual_income_impact") or 0) for r in year_records if r.get("actual_income_impact")),
        2,
    )
    has_impact = any(float(r.get("actual_income_impact") or 0) != 0 for r in year_records)
    return {
        "record_count": len(records),
        "investments_this_year": len(year_records),
        "total_invested_this_year": total_invested,
        "actual_income_impact_this_year": total_impact if has_impact else None,
        "status": "unknown" if not records else "ok",
    }
