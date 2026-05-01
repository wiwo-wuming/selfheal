"""Tests for PluginWatcher — hot-reloading file-system watcher."""

import concurrent.futures
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from selfheal.config import WatcherConfig
from selfheal.core.watchers.plugin_watcher import PluginWatcher


@pytest.fixture
def temp_plugin_dir():
    """Create a temporary directory with a plugin file."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        (root / "hot_plugin.py").write_text("""\
from selfheal.interfaces.validator import ValidatorInterface
from selfheal.events import PatchEvent, ValidationEvent

class HotValidator(ValidatorInterface):
    name = "hot_validator"

    def validate(self, patch: PatchEvent) -> ValidationEvent:
        return ValidationEvent(patch_event=patch, result="passed")
""")
        yield root


@pytest.fixture
def watcher_config(temp_plugin_dir):
    """A WatcherConfig pointing at the temp plugin dir."""
    return WatcherConfig(
        type="plugin_watcher",
        path=str(temp_plugin_dir),
        poll_interval=0.1,  # fast debounce for tests
        watch_patterns=["*.py"],
    )


@pytest.fixture
def watcher(watcher_config):
    """Create a PluginWatcher."""
    return PluginWatcher(watcher_config)


class TestPluginWatcherBasics:
    """Tests for PluginWatcher initialization and basic properties."""

    def test_name(self, watcher):
        assert watcher.name == "plugin_watcher"

    def test_initial_load_on_start(self, watcher, watcher_config, temp_plugin_dir):
        """start() should load existing plugins from the directory."""
        callback = MagicMock()

        # Patch the internal PluginLoader to avoid side effects
        with patch.object(watcher._loader, "load_from_path") as mock_load:
            # Also patch the watch loop to return immediately
            with patch.object(watcher, "_watch_loop") as mock_loop:
                watcher.start([str(temp_plugin_dir)], callback)

                mock_load.assert_called_once_with(temp_plugin_dir)

        watcher.stop()

    def test_start_creates_directory(self, watcher_config):
        """start() should create the plugin dir if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmp:
            nonexistent = Path(tmp) / "new_plugins"
            config = WatcherConfig(
                type="plugin_watcher",
                path=str(nonexistent),
                poll_interval=0.1,
            )
            w = PluginWatcher(config)
            callback = MagicMock()

            with patch.object(w, "_watch_loop"):
                w.start([str(nonexistent)], callback)

            assert nonexistent.exists()
            w.stop()

    def test_stop_stops_thread(self, watcher):
        """stop() should stop the monitoring thread."""
        callback = MagicMock()
        with patch.object(watcher, "_watch_loop"):
            watcher.start(["tests/"], callback)
            assert watcher._running
            watcher.stop()
            assert not watcher._running

    def test_stop_when_not_running(self, watcher):
        """stop() when not running should not crash."""
        watcher.stop()  # Should be a no-op


class TestPluginWatcherSchedule:
    """Tests for debounced reload scheduling."""

    def test_schedule_reload_adds_to_pending(self, watcher):
        """_schedule_reload should add a file to the pending set."""
        watcher._schedule_reload("/tmp/test_plugin.py")
        assert len(watcher._pending) == 1
        assert "/tmp/test_plugin.py" in watcher._pending

    def test_schedule_reload_deduplicates(self, watcher):
        """Multiple schedules for the same file should not duplicate."""
        watcher._schedule_reload("/tmp/test_plugin.py")
        watcher._schedule_reload("/tmp/test_plugin.py")
        watcher._schedule_reload("/tmp/test_plugin.py")
        assert len(watcher._pending) == 1

    def test_schedule_reload_multiple_files(self, watcher):
        """Different files should each have their own pending entry."""
        watcher._schedule_reload("/tmp/a.py")
        watcher._schedule_reload("/tmp/b.py")
        watcher._schedule_reload("/tmp/c.py")
        assert len(watcher._pending) == 3

    def test_process_pending_after_debounce(self, watcher, temp_plugin_dir):
        """Pending reloads should be processed after debounce period."""
        callback = MagicMock()

        # Schedule a reload
        plugin_path = str(temp_plugin_dir / "hot_plugin.py")
        watcher._schedule_reload(plugin_path)

        # Should not process immediately (debounce not elapsed)
        watcher._process_pending_reloads(callback)
        assert len(watcher._pending) == 1  # still pending

        # Wait for debounce (_process_pending_reloads uses max(0.5, poll_interval))
        debounce = max(0.5, watcher.config.poll_interval)
        time.sleep(debounce + 0.15)

        # Now should process
        watcher._process_pending_reloads(callback)
        assert len(watcher._pending) == 0

    def test_process_pending_nonexistent_file(self, watcher):
        """Files that no longer exist should be silently skipped."""
        callback = MagicMock()
        watcher._schedule_reload("/tmp/removed_plugin.py")
        debounce = max(0.5, watcher.config.poll_interval)
        time.sleep(debounce + 0.15)
        watcher._process_pending_reloads(callback)
        assert len(watcher._pending) == 0


class TestPluginWatcherPolling:
    """Tests for the polling-based file change detection."""

    def test_snapshot_records_mtimes(self, watcher, temp_plugin_dir):
        """_snapshot_dir should record file mtimes."""
        state: dict[Path, float] = {}
        watcher._snapshot_dir(state, ["*.py"])

        assert len(state) >= 1
        hot_plugin = temp_plugin_dir / "hot_plugin.py"
        assert hot_plugin in state
        assert state[hot_plugin] > 0

    def test_detect_changes_no_change(self, watcher, temp_plugin_dir):
        """_detect_changes with no modifications should return empty."""
        state: dict[Path, float] = {}
        watcher._snapshot_dir(state, ["*.py"])
        changed = watcher._detect_changes(state, ["*.py"])
        assert changed == []

    def test_detect_changes_modified(self, watcher, temp_plugin_dir):
        """_detect_changes should detect modified files."""
        state: dict[Path, float] = {}
        watcher._snapshot_dir(state, ["*.py"])

        # Modify a file and ensure mtime is updated (Windows may need a tiny delay)
        hot_plugin = temp_plugin_dir / "hot_plugin.py"
        time.sleep(0.05)
        hot_plugin.write_text(hot_plugin.read_text() + "\n# modified\n")
        # Ensure the mtime is definitely different from the snapshot
        hot_plugin.touch()

        changed = watcher._detect_changes(state, ["*.py"])
        assert hot_plugin in changed

    def test_detect_changes_new_file(self, watcher, temp_plugin_dir):
        """_detect_changes should detect newly created files."""
        state: dict[Path, float] = {}
        watcher._snapshot_dir(state, ["*.py"])

        # Create a new file
        new_file = temp_plugin_dir / "new_hot_plugin.py"
        new_file.write_text("# new plugin\n")

        changed = watcher._detect_changes(state, ["*.py"])
        assert new_file in changed

    def test_detect_changes_deleted_file(self, watcher, temp_plugin_dir):
        """Deleted files should be removed from state."""
        state: dict[Path, float] = {}
        watcher._snapshot_dir(state, ["*.py"])

        # Delete the file
        hot_plugin = temp_plugin_dir / "hot_plugin.py"
        hot_plugin.unlink()

        watcher._detect_changes(state, ["*.py"])
        assert hot_plugin not in state

    def test_detect_changes_respects_patterns(self, watcher, temp_plugin_dir):
        """_detect_changes should only check files matching patterns."""
        state: dict[Path, float] = {}
        watcher._snapshot_dir(state, ["*.py"])

        # Create a non-matching file
        (temp_plugin_dir / "notes.txt").write_text("not a plugin")

        changed = watcher._detect_changes(state, ["*.py"])
        assert len(changed) == 0  # .txt not matched


class TestPluginWatcherIntegration:
    """Integration-style tests for hot-reload flow."""

    def test_load_and_reload_flow(self, watcher, temp_plugin_dir):
        """Full load→modify→reload cycle via PluginLoader."""
        # Initial load
        watcher._loader.load_from_path(temp_plugin_dir)

        original = watcher._loader.registry.get_validator("hot_validator")
        assert original is not None
        assert original.name == "hot_validator"

        # Modify the plugin
        hot_plugin = temp_plugin_dir / "hot_plugin.py"
        hot_plugin.write_text(hot_plugin.read_text() + "\n# v2: hot-reloaded\n")

        # Reload via load_or_reload_file
        success = watcher._loader.load_or_reload_file(hot_plugin, temp_plugin_dir)
        assert success

        reloaded = watcher._loader.registry.get_validator("hot_validator")
        assert reloaded is not None
        assert reloaded is not original  # new class after reload

    def test_start_stop_lifecycle(self, watcher, temp_plugin_dir):
        """Watcher should cleanly start and stop."""
        callback = MagicMock()

        with patch.object(watcher, "_watch_loop"):
            watcher.start([str(temp_plugin_dir)], callback)
            assert watcher._running
            assert watcher._thread is not None

            watcher.stop()
            assert not watcher._running

    def test_watchdog_fallback_to_polling(self, watcher, temp_plugin_dir):
        """When watchdog is unavailable, watcher should fall back to polling."""
        callback = MagicMock()

        with patch.object(watcher, "_try_watchdog_loop", return_value=False):
            with patch.object(watcher, "_polling_loop") as mock_polling:
                watcher._watch_loop(callback)
                mock_polling.assert_called_once_with(callback)

    def test_plugin_watcher_is_registered(self):
        """PluginWatcher should be registered in the global registry."""
        from selfheal.registry import get_registry
        registry = get_registry()
        cls = registry.get_watcher("plugin_watcher")
        assert cls is not None
        assert cls.name == "plugin_watcher"


class TestPluginWatcherIntegrity:
    """Tests for the check_integrity checksum-based verification."""

    def test_compute_checksum_returns_hex_string(self):
        """_compute_checksum should return a 64-char hex digest."""
        from pathlib import Path
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as tf:
            tf.write(b"class MyPlugin:\n    name = 'test'\n")
            tf.flush()
            checksum = PluginWatcher._compute_checksum(Path(tf.name))
        assert isinstance(checksum, str)
        assert len(checksum) == 64
        assert all(c in "0123456789abcdef" for c in checksum)

    def test_compute_checksum_deterministic(self, temp_plugin_dir):
        """Same content → same checksum."""
        path = temp_plugin_dir / "hot_plugin.py"
        c1 = PluginWatcher._compute_checksum(path)
        c2 = PluginWatcher._compute_checksum(path)
        assert c1 == c2

    def test_compute_checksum_detects_change(self, temp_plugin_dir):
        """Different content → different checksum."""
        path = temp_plugin_dir / "hot_plugin.py"
        original = PluginWatcher._compute_checksum(path)
        # Modify content
        modified_content = path.read_text() + "\n# extra line\n"
        changed_path = temp_plugin_dir / "changed.py"
        changed_path.write_text(modified_content)
        modified = PluginWatcher._compute_checksum(changed_path)
        assert original != modified

    def test_record_checksum_stores_entry(self, watcher, temp_plugin_dir):
        """_record_checksum should store file_path → hexdigest."""
        path = temp_plugin_dir / "hot_plugin.py"
        checksum = watcher._record_checksum(path)
        assert str(path) in watcher._checksums
        assert watcher._checksums[str(path)] == checksum

    def test_record_checksums_from_dir_scans_all_py_files(self, watcher, temp_plugin_dir):
        """_record_checksums_from_dir should discover all .py files."""
        # Create an additional plugin file
        extra = temp_plugin_dir / "extra_plugin.py"
        extra.write_text("class Extra:\n    name = 'extra'\n")
        # Create a non-.py file — should be skipped
        (temp_plugin_dir / "notes.txt").write_text("ignore me")
        # Create a private file — should be skipped
        (temp_plugin_dir / "_private.py").write_text("class Hidden:\n    name = 'hidden'\n")

        watcher._record_checksums_from_dir(temp_plugin_dir)

        assert str(temp_plugin_dir / "hot_plugin.py") in watcher._checksums
        assert str(extra) in watcher._checksums
        assert str(temp_plugin_dir / "notes.txt") not in watcher._checksums
        assert str(temp_plugin_dir / "_private.py") not in watcher._checksums

    def test_check_integrity_all_ok(self, watcher, temp_plugin_dir):
        """check_integrity should report all ok when nothing changed."""
        watcher._record_checksums_from_dir(temp_plugin_dir)
        result = watcher.check_integrity()
        assert result["modified"] == []
        assert result["missing"] == []
        assert len(result["ok"]) >= 1
        assert str(temp_plugin_dir / "hot_plugin.py") in result["ok"]

    def test_check_integrity_detects_modified(self, watcher, temp_plugin_dir):
        """check_integrity should detect files whose content was changed."""
        path = temp_plugin_dir / "hot_plugin.py"
        watcher._record_checksum(path)

        # Tamper with the file
        path.write_text(path.read_text() + "\n# injected malicious code\n")

        result = watcher.check_integrity()
        assert result["ok"] == []
        assert str(path) in result["modified"]
        assert result["missing"] == []

    def test_check_integrity_detects_missing(self, watcher, temp_plugin_dir):
        """check_integrity should detect files that were deleted."""
        path = temp_plugin_dir / "hot_plugin.py"
        watcher._record_checksum(path)

        # Delete the file
        path.unlink()

        result = watcher.check_integrity()
        assert str(path) in result["missing"]
        assert str(path) not in result["modified"]
        assert str(path) not in result["ok"]

    def test_check_integrity_mixed_state(self, watcher, temp_plugin_dir):
        """Check integrity with a mix of ok, modified, and missing files."""
        # Record checksums for three files
        plugin_a = temp_plugin_dir / "a.py"
        plugin_b = temp_plugin_dir / "b.py"
        plugin_c = temp_plugin_dir / "c.py"
        plugin_a.write_text("class A:\n    name = 'a'\n")
        plugin_b.write_text("class B:\n    name = 'b'\n")
        plugin_c.write_text("class C:\n    name = 'c'\n")

        watcher._record_checksum(plugin_a)
        watcher._record_checksum(plugin_b)
        watcher._record_checksum(plugin_c)

        # Keep A intact, modify B, delete C
        plugin_b.write_text("class B:\n    name = 'b'\n    hacked = True\n")
        plugin_c.unlink()

        result = watcher.check_integrity()
        assert str(plugin_a) in result["ok"]
        assert str(plugin_b) in result["modified"]
        assert str(plugin_c) in result["missing"]

    def test_check_integrity_empty_registry(self, watcher):
        """check_integrity on a watcher with no tracked files returns empty lists."""
        result = watcher.check_integrity()
        assert result == {"modified": [], "missing": [], "ok": []}

    def test_start_records_baseline_checksums(self, watcher, temp_plugin_dir):
        """After start(), the checksum registry should be populated."""
        callback = MagicMock()

        with patch.object(watcher._loader, "load_from_path"):
            with patch.object(watcher, "_watch_loop"):
                watcher.start([str(temp_plugin_dir)], callback)

        assert len(watcher._checksums) >= 1
        # Use the resolved plugin_dir (watcher resolves paths in __init__)
        expected_key = str(watcher.plugin_dir / "hot_plugin.py")
        assert expected_key in watcher._checksums
        watcher.stop()

    def test_hot_reload_updates_checksum(self, watcher, temp_plugin_dir):
        """After a hot-reload, the checksum should reflect new content."""
        path = temp_plugin_dir / "hot_plugin.py"
        # Record initial checksum
        initial = watcher._record_checksum(path)

        # Modify and simulate a successful reload
        path.write_text(path.read_text() + "\n# v2\n")
        watcher._loader.load_or_reload_file(path, temp_plugin_dir)
        watcher._record_checksum(path)  # same as what _process_pending_reloads does

        updated = watcher._checksums[str(path)]
        assert updated != initial

    def test_compute_checksum_large_file(self, tmp_path):
        """_compute_checksum handles files larger than the read chunk size."""
        large_file = tmp_path / "large.py"
        # Write ~128 KB of data (2x the 64KB chunk size)
        large_file.write_text("x = " + repr("A" * 131072) + "\n")
        checksum = PluginWatcher._compute_checksum(large_file)
        assert len(checksum) == 64

    def test_thread_safe_checksum_ops(self, watcher, temp_plugin_dir):
        """Concurrent _record_checksum calls should not corrupt the registry."""
        import concurrent.futures

        files = []
        for i in range(20):
            f = temp_plugin_dir / f"concurrent_{i}.py"
            f.write_text(f"class Plugin{i}:\n    name = 'p{i}'\n")
            files.append(f)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(watcher._record_checksum, files))

        assert len(watcher._checksums) >= 20
        # Verify all entries have valid 64-char hex digests
        for path_str, checksum in watcher._checksums.items():
            assert len(checksum) == 64, f"Invalid checksum for {path_str}"
