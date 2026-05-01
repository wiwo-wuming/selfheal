"""Pipeline stage interface for SelfHeal."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from selfheal.engine import SelfHealEngine


class PipelineStage(ABC):
    """Pluggable pipeline stage.

    Each stage receives the shared context dictionary (which carries the
    ``TestFailureEvent`` and intermediate results) together with a reference
    to the owning engine so that it can access metrics, applier, config, etc.

    Stages are discovered by the plugin loader via this interface, so
    third-party stages only need to subclass ``PipelineStage`` and set a
    ``name`` class attribute.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique stage name, e.g. 'classify', 'patch', 'report'."""
        pass

    @abstractmethod
    def process(self, context: dict[str, Any], engine: SelfHealEngine) -> dict[str, Any]:
        """Execute this pipeline stage.

        Args:
            context: Shared context dict.  Well-known keys include:
                - ``event``: ``TestFailureEvent`` (always present on entry)
                - ``classification``: ``ClassificationEvent`` (set by classify)
                - ``patches``: ``list[PatchEvent]`` (set by patch stage)
                - ``final_validation``: ``ValidationEvent`` (set by patch stage)
            engine: The owning ``SelfHealEngine`` instance.

        Returns:
            The (possibly modified) context dict.
        """
        pass
