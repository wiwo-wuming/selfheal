"""Report pipeline stage — supports multiple chained reporters."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from selfheal.interfaces.pipeline_stage import PipelineStage

if TYPE_CHECKING:
    from selfheal.engine import SelfHealEngine

logger = logging.getLogger(__name__)


class ReportStage(PipelineStage):
    """Report the validation result to one or more reporters.

    If the engine has a ``_reporters`` list (multi-reporter mode), every
    enabled reporter is called.  Otherwise the legacy single ``reporter``
    attribute is used.
    """

    name = "report"

    def process(self, context: dict[str, Any], engine: SelfHealEngine) -> dict[str, Any]:
        final = context.get("final_validation")
        if final is None:
            logger.warning("No final_validation in context, skipping report")
            return context

        reporters = getattr(engine, "_reporters", None) or [engine.reporter]

        for reporter in reporters:
            try:
                reporter.report(final)
            except Exception as exc:
                logger.error(
                    f"Reporter '{getattr(reporter, 'name', reporter.__class__.__name__)}' "
                    f"failed: {exc}"
                )

        return context
