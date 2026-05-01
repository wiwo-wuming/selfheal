"""Watcher interface."""

from abc import ABC, abstractmethod
from typing import Callable, Any


class WatcherInterface(ABC):
    """Interface for test failure watchers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the watcher name."""
        pass

    @abstractmethod
    def start(self, paths: list[str], callback: Callable[[Any], None]) -> None:
        """Start watching for failures.

        Args:
            paths: Paths to watch
            callback: Called when a failure is detected
        """
        pass

    @abstractmethod
    def stop(self) -> None:
        """Stop watching."""
        pass