"""Reporter interface."""

from abc import ABC, abstractmethod

from selfheal.events import ValidationEvent


class ReporterInterface(ABC):
    """Interface for result reporters."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the reporter name."""
        pass

    @abstractmethod
    def report(self, event: ValidationEvent) -> None:
        """Report a validation event.

        Args:
            event: The validation event to report
        """
        pass