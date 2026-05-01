"""Validator interface."""

from abc import ABC, abstractmethod

from selfheal.events import PatchEvent, ValidationEvent


class ValidatorInterface(ABC):
    """Interface for patch validators."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the validator name."""
        pass

    @abstractmethod
    def validate(self, patch: PatchEvent) -> ValidationEvent:
        """Validate a patch by running tests.

        Args:
            patch: The patch event to validate

        Returns:
            Validation event with the result
        """
        pass