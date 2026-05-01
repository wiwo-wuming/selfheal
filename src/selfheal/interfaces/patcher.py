"""Patcher interface."""

from abc import ABC, abstractmethod

from selfheal.events import ClassificationEvent, PatchEvent


class PatcherInterface(ABC):
    """Interface for patch generators."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the patcher name."""
        pass

    @abstractmethod
    def generate(self, classification: ClassificationEvent) -> PatchEvent:
        """Generate a patch for a classified failure.

        Args:
            classification: The classification event with error details

        Returns:
            Patch event with the generated patch content
        """
        pass

    def apply(self, patch: PatchEvent) -> bool:
        """Apply a patch to the codebase.

        Args:
            patch: The patch event to apply

        Returns:
            True if patch was applied successfully
        """
        raise NotImplementedError