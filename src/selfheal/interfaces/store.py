"""Store interface."""

from abc import ABC, abstractmethod
from typing import Any

from selfheal.events import (
    TestFailureEvent,
    ClassificationEvent,
    PatchEvent,
    ValidationEvent,
)


class StoreInterface(ABC):
    """Interface for event stores."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the store name."""
        pass

    @abstractmethod
    def save_events(self, events: list[Any]) -> None:
        """Save events to the store.

        Args:
            events: List of events to save
        """
        pass

    @abstractmethod
    def get_events(self, event_type: str, limit: int = 100) -> list[Any]:
        """Get events from the store.

        Args:
            event_type: Type of events to retrieve
            limit: Maximum number of events to return

        Returns:
            List of events
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """Close the store connection."""
        pass