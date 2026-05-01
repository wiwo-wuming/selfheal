"""Memory store implementation."""

from typing import Any, Optional

from selfheal.config import StoreConfig
from selfheal.events import (
    TestFailureEvent,
    ClassificationEvent,
    PatchEvent,
    ValidationEvent,
)
from selfheal.interfaces.store import StoreInterface


class MemoryStore(StoreInterface):
    """In-memory event store."""

    def __init__(self, config: StoreConfig):
        self.config = config
        self._events: list[Any] = []

    name = "memory"

    def save_events(self, events: list[Any]) -> None:
        """Save events to memory."""
        self._events.extend(events)

    def get_events(self, event_type: str, limit: int = 100) -> list[Any]:
        """Get events from memory."""
        type_map = {
            "failure": TestFailureEvent,
            "classification": ClassificationEvent,
            "patch": PatchEvent,
            "validation": ValidationEvent,
        }

        cls = type_map.get(event_type)
        if not cls:
            return []

        filtered = [e for e in self._events if isinstance(e, cls)]
        return filtered[-limit:]

    def close(self) -> None:
        """Close the store (no-op for memory)."""
        pass
