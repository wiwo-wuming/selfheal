"""Validate pipeline stage — runs validation on all generated patches."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from selfheal.events import (
    ClassificationEvent,
    ErrorSeverity,
    PatchEvent,
    ValidationEvent,
)
from selfheal.interfaces.pipeline_stage import PipelineStage

if TYPE_CHECKING:
    from selfheal.engine import SelfHealEngine

logger = logging.getLogger(__name__)


class ValidateStage(PipelineStage):
    """Validate generated patches and select the best result.

    Reads ``context["patches"]`` (set by PatchStage) and validates each
    patch via ``engine.validator``.  The first ``"passed"`` result wins;
    otherwise the first non-None result is kept.  Writes
    ``context["final_validation"]``.

    If no patches exist in the context the stage produces an ``"error"``
    ``ValidationEvent`` so downstream stages always have a result.
    """

    name = "validate"

    def process(self, context: dict[str, Any], engine: SelfHealEngine) -> dict[str, Any]:
        patches: list[PatchEvent] = context.get("patches", [])

        best_validation: Optional[ValidationEvent] = None

        for patch in patches:
            validation = engine.validator.validate(patch)
            engine.metrics.record_validation(validation.result, validation.duration)
            logger.info(
                f"Validation: {validation.result} in {validation.duration:.2f}s"
            )

            if validation.result == "passed":
                best_validation = validation
                break
            elif best_validation is None:
                best_validation = validation

        # Build final validation event
        if best_validation is not None:
            final = best_validation
        elif patches:
            final = ValidationEvent(
                patch_event=patches[-1],
                result="failed",
                error_message="All patches failed validation",
            )
        else:
            # No patches at all — build a minimal error event
            classification = context.get("classification")
            final = ValidationEvent(
                patch_event=PatchEvent(
                    classification_event=classification or ClassificationEvent(
                        original_event=context["event"],
                        category="unknown",
                        severity=ErrorSeverity.MEDIUM,
                        confidence=0.0,
                    ),
                    patch_id="no-patch",
                    patch_content="",
                    generator="none",
                ),
                result="error",
                error_message="No patches were generated",
            )

        # --- experience learning: record successful patches ---
        if final.result == "passed":
            self._record_experience(context, final)

        context["final_validation"] = final
        return context

    @staticmethod
    def _record_experience(context: dict, validation: ValidationEvent) -> None:
        """Record a successfully validated patch in the experience store."""
        try:
            from selfheal.core.experience import get_experience

            event = context.get("event")
            classification = context.get("classification")
            if event is None or classification is None:
                return

            # Handle classification as either dict or ClassificationEvent
            if isinstance(classification, dict):
                cat = classification.get("category", "unknown")
            else:
                cat = classification.category

            experience = get_experience()
            experience.record_success(
                event=event,
                classification=ClassificationEvent(
                    original_event=event,
                    category=cat,
                    severity=ErrorSeverity.MEDIUM,
                    confidence=0.0,
                ) if isinstance(classification, dict) else classification,
                patch=validation.patch_event,
            )
        except Exception:
            # Experience recording is best-effort; never break the pipeline
            logger.debug("Experience recording skipped", exc_info=True)
