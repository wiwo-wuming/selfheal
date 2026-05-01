"""Tests for PluginWatcher integration with SelfHealEngine."""

import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from selfheal.config import (
    Config,
    EngineConfig,
    PluginConfig,
    WatcherConfig,
)
from selfheal.engine import SelfHealEngine
from selfheal.events import (
    ClassificationEvent,
    ErrorSeverity,
    PatchEvent,
    TestFailureEvent,
    ValidationEvent,
)
from conftest import make_failure


# ── PluginWatcher setup integration ──────────────────────────────────


class TestPluginWatcherSetup:
    """Tests for PluginWatcher integration into SelfHealEngine."""

    def test_plugin_watcher_not_created_when_disabled(self):
        """PluginWatcher should NOT be created when plugin.enabled=False."""
        config = Config(plugin=PluginConfig(enabled=False))
        with patch.object(SelfHealEngine, "_setup_components"):
            engine = SelfHealEngine(config)
        assert engine._plugin_watcher is None
        assert not any(
            hasattr(w, "check_integrity")
            for w in engine._watchers
        )

    def test_plugin_watcher_created_when_enabled(self, temp_plugin_dir):
        """PluginWatcher should be created and added to _watchers when enabled."""
        config = Config(
            plugin=PluginConfig(
                enabled=True,
                plugin_dir=str(temp_plugin_dir),
            ),
        )
        with patch.object(SelfHealEngine, "_setup_components"):
            engine = SelfHealEngine(config)
        assert engine._plugin_watcher is not None
        assert engine._plugin_watcher in engine._watchers

    def test_plugin_watcher_uses_configured_dir(self, temp_plugin_dir):
        """PluginWatcher should use config.plugin.plugin_dir as its directory."""
        config = Config(
            plugin=PluginConfig(
                enabled=True,
                plugin_dir=str(temp_plugin_dir),
            ),
        )
        with patch.object(SelfHealEngine, "_setup_components"):
            engine = SelfHealEngine(config)
        assert engine._plugin_watcher.plugin_dir == temp_plugin_dir.resolve()


# ── check_plugin_integrity ────────────────────────────────────────────


class TestCheckPluginIntegrity:
    """Tests for engine.check_plugin_integrity()."""

    def test_returns_empty_when_no_plugin_watcher(self):
        """Without PluginWatcher, check_plugin_integrity returns empty result."""
        config = Config(plugin=PluginConfig(enabled=False))
        with patch.object(SelfHealEngine, "_setup_components"):
            engine = SelfHealEngine(config)
        result = engine.check_plugin_integrity()
        assert result == {"ok": [], "modified": [], "missing": []}

    def test_delegates_to_plugin_watcher(self, temp_plugin_dir):
        """check_plugin_integrity should delegate to the PluginWatcher."""
        config = Config(
            plugin=PluginConfig(
                enabled=True,
                plugin_dir=str(temp_plugin_dir),
            ),
        )
        with patch.object(SelfHealEngine, "_setup_components"):
            engine = SelfHealEngine(config)

        # Populate checksums then verify
        engine._plugin_watcher._record_checksums_from_dir(temp_plugin_dir)
        result = engine.check_plugin_integrity()
        assert len(result["ok"]) >= 1
        assert result["modified"] == []
        assert result["missing"] == []

    def test_detects_modified_plugin(self, temp_plugin_dir):
        """check_plugin_integrity should detect a modified plugin file."""
        config = Config(
            plugin=PluginConfig(
                enabled=True,
                plugin_dir=str(temp_plugin_dir),
            ),
        )
        with patch.object(SelfHealEngine, "_setup_components"):
            engine = SelfHealEngine(config)

        # Record baseline
        engine._plugin_watcher._record_checksums_from_dir(temp_plugin_dir)

        # Tamper
        plugin_file = temp_plugin_dir / "hot_plugin.py"
        plugin_file.write_text(plugin_file.read_text() + "\n# tampered\n")

        result = engine.check_plugin_integrity()
        assert result["modified"] != []

    def test_detects_missing_plugin(self, temp_plugin_dir):
        """check_plugin_integrity should detect a deleted plugin file."""
        config = Config(
            plugin=PluginConfig(
                enabled=True,
                plugin_dir=str(temp_plugin_dir),
            ),
        )
        with patch.object(SelfHealEngine, "_setup_components"):
            engine = SelfHealEngine(config)

        # Record baseline
        engine._plugin_watcher._record_checksum(temp_plugin_dir / "hot_plugin.py")

        # Delete
        (temp_plugin_dir / "hot_plugin.py").unlink()

        result = engine.check_plugin_integrity()
        assert result["missing"] != []


# ── Integrity check in process_failure ────────────────────────────────


class TestIntegrityCheckInProcessFailure:
    """Tests for plugin integrity gating during failure processing."""

    def _make_engine_with_mocks(self, temp_plugin_dir, **plugin_overrides):
        """Helper: create engine with mock components and PluginWatcher."""
        plugin_cfg = PluginConfig(
            enabled=True,
            plugin_dir=str(temp_plugin_dir),
            **plugin_overrides,
        )
        config = Config(
            engine=EngineConfig(max_retries=1, retry_delay=0),
            plugin=plugin_cfg,
        )
        with patch.object(SelfHealEngine, "_setup_components"):
            engine = SelfHealEngine(config)

        # Set up mock components for pipeline
        engine.classifier = MagicMock()
        engine.classifier.classify.return_value = ClassificationEvent(
            original_event=make_failure(),
            category="assertion",
            severity=ErrorSeverity.MEDIUM,
            confidence=0.8,
        )
        engine.patcher = MagicMock()
        engine.patcher.generate.return_value = PatchEvent(
            classification_event=ClassificationEvent(
                original_event=make_failure(),
                category="assertion",
                severity=ErrorSeverity.MEDIUM,
                confidence=0.8,
            ),
            patch_id=str(uuid.uuid4()),
            patch_content="# fix",
            generator="template",
        )
        engine.validator = MagicMock()
        engine.validator.validate.return_value = ValidationEvent(
            patch_event=PatchEvent(
                classification_event=ClassificationEvent(
                    original_event=make_failure(),
                    category="assertion",
                    severity=ErrorSeverity.MEDIUM,
                    confidence=0.8,
                ),
                patch_id=str(uuid.uuid4()),
                patch_content="# fix",
                generator="template",
            ),
            result="passed",
        )
        engine.reporter = MagicMock()
        engine.store = MagicMock()

        # Record baseline checksums
        engine._plugin_watcher._record_checksums_from_dir(temp_plugin_dir)
        return engine

    def test_process_failure_proceeds_when_integrity_ok(self, temp_plugin_dir):
        """process_failure should proceed normally when integrity is ok."""
        engine = self._make_engine_with_mocks(temp_plugin_dir)
        result = engine.process_failure(make_failure())
        assert result.result == "passed"

    def test_process_failure_warns_on_violation_but_continues(self, temp_plugin_dir):
        """When fail_on_integrity_violation=False, processing continues after warning."""
        engine = self._make_engine_with_mocks(
            temp_plugin_dir,
            fail_on_integrity_violation=False,
        )
        # Tamper with plugin
        plugin_file = temp_plugin_dir / "hot_plugin.py"
        plugin_file.write_text(plugin_file.read_text() + "\n# tampered\n")

        result = engine.process_failure(make_failure())
        # Should still process — just logs a warning
        assert result.result == "passed"

    def test_process_failure_aborts_on_violation_when_configured(self, temp_plugin_dir):
        """When fail_on_integrity_violation=True, processing should abort."""
        engine = self._make_engine_with_mocks(
            temp_plugin_dir,
            fail_on_integrity_violation=True,
        )
        # Tamper with plugin
        plugin_file = temp_plugin_dir / "hot_plugin.py"
        plugin_file.write_text(plugin_file.read_text() + "\n# tampered\n")

        result = engine.process_failure(make_failure())
        assert result.result == "error"
        assert "integrity" in result.error_message.lower()
        assert result.patch_event.patch_id == "integrity-violation"

    def test_process_failure_skips_integrity_check_when_disabled(self, temp_plugin_dir):
        """When check_integrity_on_failure=False, no check is performed."""
        engine = self._make_engine_with_mocks(
            temp_plugin_dir,
            check_integrity_on_failure=False,
        )
        # Tamper with plugin
        plugin_file = temp_plugin_dir / "hot_plugin.py"
        plugin_file.write_text(plugin_file.read_text() + "\n# tampered\n")

        result = engine.process_failure(make_failure())
        # Should proceed normally — no integrity check
        assert result.result == "passed"

    def test_process_failure_missing_plugin_aborts_when_configured(self, temp_plugin_dir):
        """Missing plugin file with fail_on_integrity_violation=True should abort."""
        engine = self._make_engine_with_mocks(
            temp_plugin_dir,
            fail_on_integrity_violation=True,
        )
        # Delete the plugin file
        (temp_plugin_dir / "hot_plugin.py").unlink()

        result = engine.process_failure(make_failure())
        assert result.result == "error"
        assert "integrity" in result.error_message.lower()

    def test_no_integrity_check_when_plugin_disabled(self):
        """No integrity check when plugin integration is entirely disabled."""
        config = Config(
            engine=EngineConfig(max_retries=1, retry_delay=0),
            plugin=PluginConfig(enabled=False),
        )
        with patch.object(SelfHealEngine, "_setup_components"):
            engine = SelfHealEngine(config)
        # _check_integrity_before_failure should return True (skip check)
        assert engine._check_integrity_before_failure() is True


# ── Watch lifecycle with PluginWatcher ────────────────────────────────


class TestWatchWithPluginWatcher:
    """Tests for watch() lifecycle with PluginWatcher integration."""

    def test_watch_starts_plugin_watcher_separately(self, temp_plugin_dir):
        """watch() should start PluginWatcher separately from other watchers."""
        config = Config(
            plugin=PluginConfig(
                enabled=True,
                plugin_dir=str(temp_plugin_dir),
                check_integrity_on_start=False,
            ),
        )
        with patch.object(SelfHealEngine, "_setup_components"):
            engine = SelfHealEngine(config)

        # Mock both the regular watcher and plugin watcher start methods
        with patch.object(engine._plugin_watcher, "start") as mock_pw_start:
            engine.watch(["tests/"])
            # PluginWatcher.start should be called with empty paths
            mock_pw_start.assert_called_once()

    def test_watch_skips_plugin_watcher_in_regular_loop(self, temp_plugin_dir):
        """PluginWatcher should NOT be started with regular watcher paths."""
        config = Config(
            plugin=PluginConfig(
                enabled=True,
                plugin_dir=str(temp_plugin_dir),
                check_integrity_on_start=False,
            ),
            watcher=WatcherConfig(type="pytest", path="tests/"),
        )
        with patch.object(SelfHealEngine, "_setup_components"):
            engine = SelfHealEngine(config)

        # The _watchers list includes both regular watcher and plugin watcher
        # When watch() iterates, plugin_watcher should be skipped in the main loop
        with patch.object(engine._plugin_watcher, "start") as mock_pw_start:
            engine.watch(["tests/"])
            # PluginWatcher.start called exactly once (separately, not in loop)
            mock_pw_start.assert_called_once()

    def test_watch_checks_integrity_on_start(self, temp_plugin_dir):
        """When check_integrity_on_start=True, watch() should run integrity check."""
        config = Config(
            plugin=PluginConfig(
                enabled=True,
                plugin_dir=str(temp_plugin_dir),
                check_integrity_on_start=True,
            ),
        )
        with patch.object(SelfHealEngine, "_setup_components"):
            engine = SelfHealEngine(config)

        # Record checksums so integrity check has data
        engine._plugin_watcher._record_checksums_from_dir(temp_plugin_dir)

        with patch.object(engine._plugin_watcher, "start"):
            with patch.object(engine._plugin_watcher, "check_integrity") as mock_check:
                mock_check.return_value = {"ok": ["a.py"], "modified": [], "missing": []}
                engine.watch(["tests/"])
                mock_check.assert_called_once()

    def test_shutdown_stops_plugin_watcher(self, temp_plugin_dir):
        """shutdown() should stop PluginWatcher and clear the reference."""
        config = Config(
            plugin=PluginConfig(
                enabled=True,
                plugin_dir=str(temp_plugin_dir),
            ),
        )
        with patch.object(SelfHealEngine, "_setup_components"):
            engine = SelfHealEngine(config)

        pw = engine._plugin_watcher
        with patch.object(pw, "stop") as mock_stop:
            engine.store = MagicMock()
            engine.shutdown()
            mock_stop.assert_called_once()

        assert engine._plugin_watcher is None


# ── Config backward compatibility ─────────────────────────────────────


class TestPluginConfigBackwardCompat:
    """Test that PluginConfig defaults don't break existing configs."""

    def test_default_plugin_disabled(self):
        """By default, plugin integration should be disabled."""
        config = Config()
        assert config.plugin.enabled is False
        assert config.plugin.plugin_dir == "plugins/"
        assert config.plugin.check_integrity_on_start is True
        assert config.plugin.check_integrity_on_failure is True
        assert config.plugin.fail_on_integrity_violation is False

    def test_engine_works_without_plugin_config(self):
        """Engine should work normally with default (disabled) plugin config."""
        config = Config()
        with patch.object(SelfHealEngine, "_setup_components"):
            engine = SelfHealEngine(config)
        assert engine._plugin_watcher is None
        # Should not raise
        result = engine.check_plugin_integrity()
        assert result == {"ok": [], "modified": [], "missing": []}
