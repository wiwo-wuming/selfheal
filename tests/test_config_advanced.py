"""Tests for advanced config features: multi-watcher, multi-reporter."""

import pytest

from selfheal.config import (
    WatcherConfig,
    WatcherItemConfig,
    ReporterConfig,
    ReporterItemConfig,
    GitHubConfig,
)


class TestWatcherConfigMulti:
    """Test WatcherConfig.get_watchers() in single and multi modes."""

    def test_single_watcher_legacy_mode(self):
        """Without 'watchers' list, build from legacy fields."""
        config = WatcherConfig(
            type="pytest",
            path="my_tests/",
            pytest_args=["-x"],
            poll_interval=10.0,
            watch_patterns=["*.py", "*_test.py"],
        )
        items = config.get_watchers()
        assert len(items) == 1
        w = items[0]
        assert w.type == "pytest"
        assert w.path == "my_tests/"
        assert w.pytest_args == ["-x"]
        assert w.poll_interval == 10.0
        assert w.watch_patterns == ["*.py", "*_test.py"]
        assert w.enabled is True

    def test_multi_watcher_mode(self):
        """With 'watchers' list populated, use it directly."""
        config = WatcherConfig(
            watchers=[
                WatcherItemConfig(type="pytest", path="tests/unit/"),
                WatcherItemConfig(
                    type="raw_log",
                    path="logs/",
                    watch_patterns=["*.log"],
                    poll_interval=15.0,
                ),
            ]
        )
        items = config.get_watchers()
        assert len(items) == 2
        assert items[0].type == "pytest"
        assert items[0].path == "tests/unit/"
        assert items[1].type == "raw_log"
        assert items[1].path == "logs/"
        assert items[1].watch_patterns == ["*.log"]
        assert items[1].poll_interval == 15.0

    def test_disabled_watchers_filtered_out(self):
        """Disabled watchers should be excluded from get_watchers()."""
        config = WatcherConfig(
            watchers=[
                WatcherItemConfig(type="pytest", path="tests/"),
                WatcherItemConfig(type="raw_log", path="logs/", enabled=False),
                WatcherItemConfig(type="pytest", path="tests/e2e/"),
            ]
        )
        items = config.get_watchers()
        assert len(items) == 2
        assert all(w.enabled for w in items)
        types = [w.type for w in items]
        assert types == ["pytest", "pytest"]

    def test_all_disabled_returns_empty(self):
        """When all watchers are disabled, return empty list."""
        config = WatcherConfig(
            watchers=[
                WatcherItemConfig(type="pytest", path="tests/", enabled=False),
            ]
        )
        items = config.get_watchers()
        assert items == []

    def test_default_watcher_config(self):
        """Default WatcherConfig should return one pytest watcher."""
        config = WatcherConfig()
        items = config.get_watchers()
        assert len(items) == 1
        assert items[0].type == "pytest"
        assert items[0].path == "tests/"


class TestReporterConfigMulti:
    """Test ReporterConfig.get_reporters() in single and multi modes."""

    def test_single_reporter_legacy_mode(self):
        """Without 'reporters' list, build from legacy 'type' field."""
        config = ReporterConfig(
            type="terminal",
            webhook_url="https://example.com/hook",
        )
        items = config.get_reporters()
        assert len(items) == 1
        r = items[0]
        assert r.type == "terminal"
        assert r.enabled is True

    def test_multi_reporter_mode(self):
        """With 'reporters' list populated, use it directly."""
        config = ReporterConfig(
            reporters=[
                ReporterItemConfig(type="terminal"),
                ReporterItemConfig(
                    type="webhook",
                    webhook_url="https://hooks.example.com/slack",
                    webhook_events=["passed", "failed"],
                ),
                ReporterItemConfig(
                    type="github",
                    github=GitHubConfig(owner="acme", repo="selfheal"),
                ),
            ]
        )
        items = config.get_reporters()
        assert len(items) == 3
        assert items[0].type == "terminal"
        assert items[1].type == "webhook"
        assert items[1].webhook_url == "https://hooks.example.com/slack"
        assert items[1].webhook_events == ["passed", "failed"]
        assert items[2].type == "github"
        assert items[2].github.owner == "acme"

    def test_disabled_reporters_filtered_out(self):
        """Disabled reporters should be excluded."""
        config = ReporterConfig(
            reporters=[
                ReporterItemConfig(type="terminal"),
                ReporterItemConfig(type="webhook", enabled=False),
            ]
        )
        items = config.get_reporters()
        assert len(items) == 1
        assert items[0].type == "terminal"

    def test_all_disabled_returns_empty(self):
        """When all reporters are disabled, return empty list."""
        config = ReporterConfig(
            reporters=[
                ReporterItemConfig(type="terminal", enabled=False),
            ]
        )
        items = config.get_reporters()
        assert items == []

    def test_default_reporter_config(self):
        """Default ReporterConfig should return one terminal reporter."""
        config = ReporterConfig()
        items = config.get_reporters()
        assert len(items) == 1
        assert items[0].type == "terminal"
