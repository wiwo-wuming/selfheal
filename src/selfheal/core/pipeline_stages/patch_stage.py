"""Patch generation + apply pipeline stage (with retry and dry-run support)."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from selfheal.events import PatchEvent
from selfheal.interfaces.pipeline_stage import PipelineStage

if TYPE_CHECKING:
    from selfheal.engine import SelfHealEngine

logger = logging.getLogger(__name__)


class PatchStage(PipelineStage):
    """Generate and optionally apply patches with retry.

    This stage encapsulates the inner retry loop that was previously
    hard-coded inside ``SelfHealEngine.process_failure``.

    Dry-run mode: when ``engine.config.engine.dry_run = True``, patches are
    generated and previewed but never applied. Validation still runs against
    the original (unmodified) code.

    Validation is handled by the separate ``ValidateStage`` which reads
    ``context["patches"]`` produced here.
    """

    name = "patch"

    def process(self, context: dict[str, Any], engine: SelfHealEngine) -> dict[str, Any]:
        event = context["event"]
        classification = context["classification"]
        engine_cfg = engine.config.engine

        all_patches: list[PatchEvent] = []

        for attempt in range(engine_cfg.max_retries):
            if attempt > 0:
                engine.metrics.record_retry()
                logger.info(
                    f"Retry attempt {attempt + 1}/{engine_cfg.max_retries}"
                )
                time.sleep(engine_cfg.retry_delay)

            # Generate patch
            patch = engine.patcher.generate(classification)

            # Resolve target file
            if not patch.target_file:
                patch.target_file = engine._resolve_target_file(event.test_path)

            logger.info(
                f"Generated patch: {patch.patch_id} -> {patch.target_file}"
            )

            # Dry-run mode: preview but don't apply
            if engine_cfg.dry_run:
                preview = engine.applier.dry_run_preview(patch)
                logger.info("Dry-run preview:\n%s", preview)
                patch.status = "dry_run"
                engine.metrics.record_patch("dry_run")
                all_patches.append(patch)
                # Don't retry in dry-run mode; one preview is enough
                continue

            # Apply patch (if auto_apply is enabled)
            if engine_cfg.auto_apply and patch.target_file:
                apply_ok = engine.applier.apply(patch)
                engine.metrics.record_patch(patch.status)
                all_patches.append(patch)
                if not apply_ok:
                    logger.warning(
                        f"Patch {patch.patch_id} failed to apply, retrying..."
                    )
                    if engine_cfg.strategy_fallback and attempt < engine_cfg.max_retries - 1:
                        continue
            else:
                patch.status = "pending_review" if not engine_cfg.auto_apply else "generated"
                if patch.target_file and patch.patch_content:
                    patch.suggested_command = (
                        f"python -m selfheal apply {patch.patch_id} "
                        f"--target {patch.target_file}"
                    )
                # Record the actual generation result, not the review state
                engine.metrics.record_patch("generated")
                all_patches.append(patch)

        context["patches"] = all_patches
        return context
