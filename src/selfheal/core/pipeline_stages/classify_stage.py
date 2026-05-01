"""Classify pipeline stage."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from selfheal.interfaces.pipeline_stage import PipelineStage

if TYPE_CHECKING:
    from selfheal.engine import SelfHealEngine

logger = logging.getLogger(__name__)


class ClassifyStage(PipelineStage):
    """Classify a test failure into a category + severity + confidence."""

    name = "classify"

    def process(self, context: dict[str, Any], engine: SelfHealEngine) -> dict[str, Any]:
        event = context["event"]
        classification = engine.classifier.classify(event)

        engine.metrics.record_classification(
            classification.category, classification.severity.value
        )
        logger.info(
            f"Classified: {classification.category} "
            f"(confidence: {classification.confidence:.2f})"
        )

        context["classification"] = classification
        return context
