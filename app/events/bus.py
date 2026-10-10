"""Internal event bus."""

from collections.abc import Callable
from contextvars import ContextVar

from sqlmodel import Session

from app.events.event_log import event_log_writer
from app.events.types import Event

Handler = Callable[[Event], None]

# Per-request session (avoids cross-request races on a global singleton).
_event_session: ContextVar[Session | None] = ContextVar("event_session", default=None)


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}
        self._global: list[Handler] = []

    def set_session(self, session: Session | None) -> None:
        _event_session.set(session)

    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def subscribe_all(self, handler: Handler) -> None:
        self._global.append(handler)

    def emit(self, event: Event, session: Session | None = None) -> None:
        db = session if session is not None else _event_session.get()
        event_log_writer.write(event, db)
        for handler in self._global:
            try:
                handler(event)
            except Exception:
                pass
        for handler in self._handlers.get(event.event_type, []):
            try:
                handler(event)
            except Exception:
                pass
        for handler in self._handlers.get("*", []):
            try:
                handler(event)
            except Exception:
                pass


event_bus = EventBus()
