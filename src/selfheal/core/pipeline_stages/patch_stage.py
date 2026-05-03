"""Patch generation + apply pipeline stage (with retry and dry-run support)."""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Any

from selfheal.events import PatchEvent
from selfheal.interfaces.pipeline_stage import PipelineStage

if TYPE_CHECKING:
    from selfheal.engine import SelfHealEngine

logger = logging.getLogger(__name__)

# Patterns indicating a defensive/low-quality patch
_QUALITY_ISSUE_PATTERNS = [
    (r"(?m)^\+\s*pass\s*(#.*)?$", "empty pass statement added"),
    (r"pytest\.skip\(", "pytest.skip() hides the error"),
    (r"pytest\.xfail\(", "pytest.xfail() marks failure as expected"),
    (r"pytest\.importorskip\(", "importorskip bypasses import error"),
    (r"(?m)^\+.*try:\s*$", "bare try may swallow errors without logging"),
]


def _check_patch_quality(patch_content: str) -> list[str]:
    """Check a patch for defensive-only patterns.

    Returns a list of quality issue descriptions (empty = good quality).
    """
    issues = []
    for pattern, desc in _QUALITY_ISSUE_PATTERNS:
        if re.search(pattern, patch_content):
            issues.append(desc)
    return issues


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

            # Generate patch (use LLM fallback on retry if available)
            if attempt > 0 and engine._llm_patcher is not None:
                logger.info("Strategy fallback: switching to LLM patcher for retry")
                patch = engine._llm_patcher.generate(classification)
            else:
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
                        f"python -m selfheal apply --input <patch.json> "
                        f"--target {patch.target_file}"
                    )
                    # Quality check: detect defensive-only patches
                    quality_issues = _check_patch_quality(patch.patch_content)
                    if quality_issues:
                        logger.warning(
                            "Patch %s has quality issues: %s",
                            patch.patch_id, ", ".join(quality_issues),
                        )
                        patch.status = "low_quality"
                # Record the actual generation result, not the review state
                engine.metrics.record_patch(patch.status)
                all_patches.append(patch)

        context["patches"] = all_patches
        return context
