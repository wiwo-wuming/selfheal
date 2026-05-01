"""Core engine for SelfHeal."""

import logging
import time
from pathlib import Path
from typing import Optional

from selfheal.config import Config, WatcherConfig
from selfheal.core import register_defaults  # noqa: F401 - triggers auto-registration
from selfheal.core.applier import PatchApplier
from selfheal.core.hooks import Hook, MetricsHook
from selfheal.core.metrics import MetricsCollector
from selfheal.events import (
    ErrorSeverity,
    TestFailureEvent,
    ClassificationEvent,
    PatchEvent,
    ValidationEvent,
)
from selfheal.interfaces.pipeline_stage import PipelineStage
from selfheal.registry import get_registry

logger = logging.getLogger(__name__)


class SelfHealEngine:
    """Main engine orchestrating the self-healing pipeline.

    Pipeline: Watch -> Classify -> Patch -> Validate -> Report -> Store
    With built-in retry, rollback, metrics, and hook observers.

    The pipeline is pluggable — stages are instantiated from the registry
    based on ``config.pipeline.stages``.  Third-party stages can be registered
    and swapped in without touching engine code.

    Hooks fire before and after every pipeline stage.  The default
    ``MetricsHook`` logs per-stage timing; custom hooks can be passed via
    the ``hooks`` parameter.

    Plugin Integration:
        When ``config.plugin.enabled`` is True, the engine automatically
        creates a :class:`PluginWatcher` and integrates it into the watcher
        list.  The engine exposes :meth:`check_plugin_integrity` which
        verifies that loaded plugins have not been tampered with.  Integrity
        checks are performed at start-up and (optionally) before each
        failure is processed.
    """

    # Map: (component_category, attr_name_on_engine)
    _COMPONENT_SPEC = [
        ("watcher", "watcher"),
        ("classifier", "classifier"),
        ("patcher", "patcher"),
        ("validator", "validator"),
        ("reporter", "reporter"),
        ("store", "store"),
    ]

    def __init__(self, config: Optional[Config] = None, hooks: Optional[list[Hook]] = None):
        self.config = config or Config.load_default()
        self.registry = get_registry()
        self.metrics = MetricsCollector()
        self.applier = PatchApplier(self.config.engine)
        self._pipeline: list[PipelineStage] = []
        self._reporters: list = []  # multi-reporter chain (populated below)
        self._watchers: list = []   # multi-watcher support (populated below)
        self._hooks: list[Hook] = hooks or [MetricsHook()]
        self._plugin_watcher: Optional[object] = None  # set in _setup_plugin_watcher
        self._setup_components()
        self._setup_reporters()
        self._setup_watchers()
        self._setup_plugin_watcher()
        self._setup_pipeline()

    def _setup_components(self) -> None:
        """Initialize components from registry based on config."""
        for category, attr_name in self._COMPONENT_SPEC:
            config_section = getattr(self.config, category)
            type_name = config_section.type
            getter = getattr(self.registry, f"get_{category}")
            cls = getter(type_name)
            if cls is None:
                raise ValueError(f"Unknown {category} type: {type_name}")
            setattr(self, attr_name, cls(config_section))

    def _setup_reporters(self) -> None:
        """Build the multi-reporter chain from config.

        If ``ReporterConfig.reporters`` is populated, instantiate every
        enabled item.  Otherwise the legacy single ``self.reporter`` is
        sufficient and ``_reporters`` stays empty.
        """
        reporter_items = self.config.reporter.get_reporters()
        if len(reporter_items) <= 1:
            return  # single-reporter mode — use self.reporter directly

        for item in reporter_items:
            cls = self.registry.get_reporter(item.type)
            if cls is None:
                logger.warning(f"Unknown reporter type: {item.type}, skipping")
                continue
            self._reporters.append(cls(item))

    def _setup_pipeline(self) -> None:
        """Build the pipeline stage list from config.

        Stages are looked up from the registry by name.  If a stage is
        unknown or disabled it is silently skipped.
        """
        pipeline_cfg = self.config.get_effective_pipeline()
        for stage_cfg in pipeline_cfg.stages:
            if not stage_cfg.enabled:
                continue
            stage_cls = self.registry.get_stage(stage_cfg.type)
            if stage_cls is None:
                logger.warning(
                    f"Unknown pipeline stage '{stage_cfg.type}', skipping"
                )
                continue
            self._pipeline.append(stage_cls())

    def _resolve_target_file(self, test_path: str) -> Optional[str]:
        """Resolve the likely source file for a given test path.

        E.g. tests/test_foo.py -> src/foo.py or foo.py
        """
        import re
        test_p = Path(test_path.split("::")[0])

        # Strategy 1: Direct mapping tests/test_X -> X
        if test_p.parent.name == "tests" or "test" in str(test_p.parent):
            source_name = re.sub(r"^test_", "", test_p.stem)
            candidates = [
                test_p.parent.parent / (source_name + ".py"),
                test_p.parent.parent / "src" / (source_name + ".py"),
                Path.cwd() / (source_name + ".py"),
                Path.cwd() / "src" / (source_name + ".py"),
            ]
            for candidate in candidates:
                if candidate.exists():
                    return str(candidate)

        # Strategy 2: Try the test_path parent directory
        parent_dir = test_p.parent
        if parent_dir.exists():
            source_name = re.sub(r"^test_", "", test_p.stem)
            candidate = parent_dir / (source_name + ".py")
            if candidate.exists():
                return str(candidate)

        return None

    def process_failure(self, event: TestFailureEvent) -> ValidationEvent:
        """Process a test failure through the pluggable pipeline.

        Hooks fire before and after every pipeline stage.  A failing hook
        is logged and ignored so it never aborts the pipeline.

        If plugin integrity checking is enabled and a violation is
        detected with ``fail_on_integrity_violation=True``, the method
        returns an error ValidationEvent immediately.
        """
        logger.info(f"Processing failure: {event.test_path}")
        self.metrics.record_failure()

        # Plugin integrity gate
        if not self._check_integrity_before_failure():
            return ValidationEvent(
                patch_event=PatchEvent(
                    classification_event=ClassificationEvent(
                        original_event=event,
                        category="unknown",
                        severity=ErrorSeverity.HIGH,
                        confidence=0.0,
                    ),
                    patch_id="integrity-violation",
                    patch_content="",
                    generator="none",
                ),
                result="error",
                error_message="Plugin integrity violation detected; processing aborted",
            )

        pipeline_start = time.time()

        # Run pipeline stages with hooks
        context: dict = {"event": event}
        for stage in self._pipeline:
            stage_name = stage.name

            # --- before-stage hooks ---
            for hook in self._hooks:
                try:
                    hook.before_stage(stage_name, context, self)
                except Exception as exc:
                    logger.warning(
                        "Hook %s.before_stage(%s) failed: %s",
                        hook.__class__.__name__, stage_name, exc,
                    )

            # --- execute stage ---
            stage_error: Exception | None = None
            try:
                context = stage.process(context, self)
            except Exception as exc:
                logger.error(
                    f"Pipeline stage '{stage_name}' failed: {exc}", exc_info=True
                )
                stage_error = exc
                context["_error"] = str(exc)

            # --- after-stage hooks ---
            for hook in self._hooks:
                try:
                    hook.after_stage(stage_name, context, self, error=stage_error)
                except Exception as exc:
                    logger.warning(
                        "Hook %s.after_stage(%s) failed: %s",
                        hook.__class__.__name__, stage_name, exc,
                    )

        final = context.get("final_validation")
        if final is None:
            final = ValidationEvent(
                patch_event=PatchEvent(
                    classification_event=ClassificationEvent(
                        original_event=event,
                        category="unknown",
                        severity=ErrorSeverity.MEDIUM,
                        confidence=0.0,
                    ),
                    patch_id="pipeline-error",
                    patch_content="",
                    generator="none",
                ),
                result="error",
                error_message=context.get("_error", "Pipeline did not produce a result"),
            )

        pipeline_duration = time.time() - pipeline_start
        self.metrics.record_pipeline_run(pipeline_duration)
        return final

    def process_batch(self, events: list[TestFailureEvent]) -> list[ValidationEvent]:
        """Process multiple failures in batch."""
        results = []
        for event in events:
            result = self.process_failure(event)
            results.append(result)
        return results

    def _setup_watchers(self) -> None:
        """Build the watcher list from config.

        For single-watcher mode ``self.watcher`` is set directly (backward
        compat).  When multiple watchers are configured they are stored in
        ``self._watchers`` and ``self.watcher`` points to the first enabled one.
        """
        watcher_items = self.config.watcher.get_watchers()
        if not watcher_items:
            return

        for i, item in enumerate(watcher_items):
            cls = self.registry.get_watcher(item.type)
            if cls is None:
                logger.warning(f"Unknown watcher type: {item.type}, skipping")
                continue
            instance = cls(item)
            self._watchers.append(instance)
            if i == 0:
                # Backward compat: self.watcher points to the first watcher
                self.watcher = instance

    def _setup_plugin_watcher(self) -> None:
        """Set up the PluginWatcher for hot-reloading integration.

        When ``config.plugin.enabled`` is True, creates a PluginWatcher
        instance and adds it to ``self._watchers``.  The watcher is also
        stored in ``self._plugin_watcher`` for integrity-check access.

        This is separate from ``_setup_watchers`` because the plugin
        watcher is configured via ``config.plugin`` rather than
        ``config.watcher.watchers``.
        """
        plugin_cfg = self.config.plugin
        if not plugin_cfg.enabled:
            return

        from selfheal.core.watchers.plugin_watcher import PluginWatcher

        watcher_config = WatcherConfig(
            type="plugin_watcher",
            path=plugin_cfg.plugin_dir,
            poll_interval=2.0,
            watch_patterns=["*.py"],
        )
        pw = PluginWatcher(watcher_config)
        self._plugin_watcher = pw
        self._watchers.append(pw)
        logger.info(f"PluginWatcher integrated (dir={plugin_cfg.plugin_dir})")

    def check_plugin_integrity(self) -> dict[str, list[str]]:
        """Verify integrity of all tracked plugin files.

        Delegates to :meth:`PluginWatcher.check_integrity` if a
        PluginWatcher is active.  Returns an empty result when plugin
        integration is disabled.

        Returns:
            dict with keys ``ok``, ``modified``, ``missing`` — each a list
            of file paths.
        """
        if self._plugin_watcher is None:
            return {"ok": [], "modified": [], "missing": []}
        return self._plugin_watcher.check_integrity()

    def _check_integrity_before_failure(self) -> bool:
        """Run plugin integrity check before processing a failure.

        Returns True if processing should continue, False if a violation
        was detected and ``fail_on_integrity_violation`` is True.
        """
        if self._plugin_watcher is None:
            return True
        if not self.config.plugin.check_integrity_on_failure:
            return True

        result = self.check_plugin_integrity()
        has_violation = bool(result["modified"] or result["missing"])

        if has_violation:
            logger.warning(
                "Plugin integrity violation detected — "
                "modified: %s, missing: %s",
                result["modified"], result["missing"],
            )
            if self.config.plugin.fail_on_integrity_violation:
                logger.error(
                    "Aborting failure processing due to plugin integrity violation"
                )
                return False

        return True

    def watch(self, paths: list[str]) -> None:
        """Start watching for test failures via all configured watchers.

        Also starts the PluginWatcher if plugin integration is enabled,
        and performs a start-up integrity check if configured.
        """
        for w in self._watchers:
            if w is self._plugin_watcher:
                # PluginWatcher monitors its own plugin_dir, not test paths
                continue
            w.start(paths, self.process_failure)

        # Start plugin watcher separately (it monitors plugin_dir, not test paths)
        if self._plugin_watcher is not None:
            self._plugin_watcher.start([], lambda e: None)
            logger.info("PluginWatcher started with hot-reload monitoring")

            # Optionally verify integrity at start-up
            if self.config.plugin.check_integrity_on_start:
                result = self.check_plugin_integrity()
                if result["modified"] or result["missing"]:
                    logger.warning(
                        "Plugin integrity check at start-up detected issues — "
                        "modified: %s, missing: %s",
                        result["modified"], result["missing"],
                    )
                else:
                    logger.info("Plugin integrity check at start-up: all ok")

    def shutdown(self) -> None:
        """Shutdown the engine and cleanup resources."""
        for w in self._watchers:
            w.stop()
        self._plugin_watcher = None
        self.store.close()
        logger.info("Engine shutdown complete")

    def get_metrics_report(self) -> str:
        """Get a formatted metrics report."""
        return self.metrics.format_report()
