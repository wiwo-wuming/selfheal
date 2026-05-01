"""Pipeline hooks for SelfHeal.

Hooks are lightweight observers that fire before and after each pipeline
stage.  They are purely decorative — a failing hook never aborts the
pipeline, and hooks cannot mutate the context.

Built-in hooks
--------------
* ``MetricsHook`` — records per-stage timing and logs it.

Custom hooks
------------
Subclass ``Hook``, implement ``before_stage`` / ``after_stage``, and pass
instances to ``SelfHealEngine(hooks=[...])``.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from selfheal.engine import SelfHealEngine

logger = logging.getLogger(__name__)


class Hook(ABC):
    """Observer that fires before/after each pipeline stage.

    Hooks are **read-only** observers — they receive the context dict but
    must not modify it.  If a hook raises an exception it is logged and
    swallowed so the pipeline continues unaffected.
    """

    @abstractmethod
    def before_stage(
        self, stage_name: str, context: dict[str, Any], engine: SelfHealEngine
    ) -> None:
        """Called immediately before *stage_name* executes."""

    @abstractmethod
    def after_stage(
        self,
        stage_name: str,
        context: dict[str, Any],
        engine: SelfHealEngine,
        error: Exception | None = None,
    ) -> None:
        """Called immediately after *stage_name* completes.

        *error* is ``None`` on success, or the exception that was raised.
        """


class MetricsHook(Hook):
    """Built-in hook that records per-stage duration and logs it."""

    def before_stage(
        self, stage_name: str, context: dict[str, Any], engine: SelfHealEngine
    ) -> None:
        """Store the wall-clock time before the stage runs."""
        context.setdefault("_hook_timers", {})
        context["_hook_timers"][stage_name] = time.monotonic()

    def after_stage(
        self,
        stage_name: str,
        context: dict[str, Any],
        engine: SelfHealEngine,
        error: Exception | None = None,
    ) -> None:
        """Log the elapsed time for the stage."""
        timers = context.get("_hook_timers", {})
        start = timers.get(stage_name)
        if start is None:
            return
        elapsed = time.monotonic() - start
        status = "FAILED" if error else "OK"
        logger.info("[Hook] stage=%s elapsed=%.3fs status=%s", stage_name, elapsed, status)
        # Also feed into the engine's metrics
        engine.metrics._pipeline_times.setdefault(stage_name, []).append(elapsed)
