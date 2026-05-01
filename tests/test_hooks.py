"""Tests for pipeline hooks: MetricsHook and custom hooks."""

import logging
import time
from unittest.mock import MagicMock

import pytest

from selfheal.core.hooks import Hook, MetricsHook


class TestMetricsHook:
    """Test the built-in MetricsHook behaviour."""

    def test_before_stage_sets_timer(self):
        hook = MetricsHook()
        context: dict = {}
        engine = MagicMock()

        hook.before_stage("classify", context, engine)

        assert "_hook_timers" in context
        assert "classify" in context["_hook_timers"]
        assert context["_hook_timers"]["classify"] > 0

    def test_before_stage_preserves_existing_timers(self):
        hook = MetricsHook()
        context = {"_hook_timers": {"patch": 100.0}}
        engine = MagicMock()

        hook.before_stage("classify", context, engine)

        assert context["_hook_timers"]["patch"] == 100.0
        assert "classify" in context["_hook_timers"]

    def test_before_stage_overwrites_same_stage_timer(self):
        hook = MetricsHook()
        context = {"_hook_timers": {"classify": 50.0}}
        engine = MagicMock()

        hook.before_stage("classify", context, engine)

        assert context["_hook_timers"]["classify"] > 50.0  # updated

    def test_after_stage_logs_success(self, caplog):
        hook = MetricsHook()
        context = {"_hook_timers": {"classify": time.monotonic() - 0.01}}
        engine = MagicMock()
        engine.metrics._pipeline_times = {}

        with caplog.at_level(logging.INFO, logger="selfheal.core.hooks"):
            hook.after_stage("classify", context, engine, error=None)

        assert "[Hook]" in caplog.text
        assert "status=OK" in caplog.text
        assert "classify" in caplog.text

    def test_after_stage_logs_failure(self, caplog):
        hook = MetricsHook()
        context = {"_hook_timers": {"classify": time.monotonic() - 0.01}}
        engine = MagicMock()
        engine.metrics._pipeline_times = {}
        test_error = ValueError("something broke")

        with caplog.at_level(logging.INFO, logger="selfheal.core.hooks"):
            hook.after_stage("classify", context, engine, error=test_error)

        assert "status=FAILED" in caplog.text

    def test_after_stage_no_timer_does_not_crash(self):
        hook = MetricsHook()
        context: dict = {}
        engine = MagicMock()

        # Should not raise
        hook.after_stage("classify", context, engine, error=None)

    def test_after_stage_writes_to_engine_metrics(self):
        hook = MetricsHook()
        context = {"_hook_timers": {"patch": time.monotonic() - 0.05}}
        engine = MagicMock()
        engine.metrics._pipeline_times = {}

        hook.after_stage("patch", context, engine, error=None)

        assert "patch" in engine.metrics._pipeline_times
        assert len(engine.metrics._pipeline_times["patch"]) == 1
        assert engine.metrics._pipeline_times["patch"][0] > 0


class TestCustomHook:
    """Test that custom Hook subclasses work correctly."""

    def test_custom_hook_before_and_after_called(self):
        """A custom hook should receive before_stage and after_stage calls."""
        call_log = []

        class TracingHook(Hook):
            def before_stage(self, stage_name, context, engine):
                call_log.append(("before", stage_name))

            def after_stage(self, stage_name, context, engine, error=None):
                call_log.append(("after", stage_name, error is not None))

        hook = TracingHook()
        engine = MagicMock()

        hook.before_stage("classify", {}, engine)
        hook.after_stage("classify", {}, engine, error=None)

        assert call_log == [("before", "classify"), ("after", "classify", False)]

    def test_custom_hook_with_error(self):
        call_log = []

        class AlertHook(Hook):
            def before_stage(self, stage_name, context, engine):
                pass

            def after_stage(self, stage_name, context, engine, error=None):
                if error:
                    call_log.append(("alert", stage_name, str(error)))

        hook = AlertHook()
        engine = MagicMock()
        test_err = RuntimeError("stage blew up")

        hook.after_stage("patch", {}, engine, error=test_err)

        assert len(call_log) == 1
        assert call_log[0] == ("alert", "patch", "stage blew up")

    def test_hook_exception_does_not_propagate_by_convention(self):
        """Hooks are documented as not propagating exceptions.
        The engine wraps hooks in try/except; we verify the hook itself
        can raise without any special handling needed."""

        class BrokenHook(Hook):
            def before_stage(self, stage_name, context, engine):
                raise RuntimeError("hook bug")

            def after_stage(self, stage_name, context, engine, error=None):
                pass  # ok

        hook = BrokenHook()
        engine = MagicMock()

        # The hook should raise normally — it's the engine's job to catch it
        with pytest.raises(RuntimeError, match="hook bug"):
            hook.before_stage("classify", {}, engine)
