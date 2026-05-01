"""Store pipeline stage — persists the event chain."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from selfheal.interfaces.pipeline_stage import PipelineStage

if TYPE_CHECKING:
    from selfheal.engine import SelfHealEngine

logger = logging.getLogger(__name__)


class StoreStage(PipelineStage):
    """Persist the full event chain to the configured store."""

    name = "store"

    def process(self, context: dict[str, Any], engine: SelfHealEngine) -> dict[str, Any]:
        event = context["event"]
        classification = context.get("classification")
        patches = context.get("patches", [])
        final = context.get("final_validation")

        events = [event]
        if classification is not None:
            events.append(classification)
        events.extend(patches)
        if final is not None:
            events.append(final)

        engine.store.save_events(events)
        logger.debug(f"Stored {len(events)} events")
        return context
