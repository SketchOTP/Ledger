"""Append-only event log persistence."""

import json
from pathlib import Path

from sqlmodel import Session

from app.config import settings
from app.db.models import EventLogEntry
from app.events.types import Event


class EventLogWriter:
    """Dual-write: SQLite + file-based append log."""

    def __init__(self, log_dir: Path | None = None) -> None:
        self.log_dir = log_dir or settings.event_log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def write(self, event: Event, session: Session | None = None) -> None:
        line = event.model_dump_json()
        day_file = self.log_dir / f"events-{event.created_at[:10]}.jsonl"
        with day_file.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

        if session is not None:
            entry = EventLogEntry(
                event_id=event.event_id,
                event_type=event.event_type,
                source=event.source,
                payload_json=json.dumps(event.payload),
                correlation_id=event.correlation_id,
                severity=event.severity,
            )
            session.add(entry)
            # Do not flush here — nested flush during an in-flight commit causes
            # "Session is already flushing" when multiple requests share timing.
            # The request/session scope commits pending event rows.


event_log_writer = EventLogWriter()
