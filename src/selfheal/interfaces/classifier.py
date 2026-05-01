"""Classifier interface."""

from abc import ABC, abstractmethod

from selfheal.events import TestFailureEvent, ClassificationEvent


class ClassifierInterface(ABC):
    """Interface for error classifiers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the classifier name."""
        pass

    @abstractmethod
    def classify(self, event: TestFailureEvent) -> ClassificationEvent:
        """Classify a test failure event.

        Args:
            event: The test failure event to classify

        Returns:
            Classification event with category, severity, and confidence
        """
        pass