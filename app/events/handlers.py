"""Default event handlers."""

from app.events.types import Event


def log_critical_events(event: Event) -> None:
    """Placeholder for alerting on critical events."""
    if event.severity == "critical":
        pass  # Future: notifications


def register_default_handlers(bus) -> None:
    bus.subscribe_all(log_critical_events)
