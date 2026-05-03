"""Comprehensive tests for config module: env var resolution, save/load roundtrip, LLM config, edge cases."""

import json
import os
import tempfile
from pathlib import Path

import pytest
import yaml

from selfheal.config import (
    Config,
    ClassifierConfig,
    EngineConfig,
    LLMConfig,
    PatcherConfig,
    PipelineConfig,
    PipelineStageConfig,
    ReporterConfig,
    ReporterItemConfig,
    RuleConfig,
    StoreConfig,
    ValidatorConfig,
    WatcherConfig,
    WatcherItemConfig,
    _resolve_env,
)


class TestEnvVarResolution:
    """Test environment variable placeholder resolution in config values."""

    def test_resolve_simple_var(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "secret123")
        assert _resolve_env("${MY_KEY}") == "secret123"

    def test_resolve_var_with_default(self):
        assert _resolve_env("${NONEXISTENT_VAR:-fallback}") == "fallback"

    def test_resolve_var_without_default_missing(self):
        assert _resolve_env("${NONEXISTENT_VAR}") == ""

    def test_resolve_multiple_vars(self, monkeypatch):
        monkeypatch.setenv("HOST", "localhost")
        monkeypatch.setenv("PORT", "8080")
        result = _resolve_env("${HOST}:${PORT}")
        assert result == "localhost:8080"

    def test_resolve_mixed_text_and_vars(self, monkeypatch):
        monkeypatch.setenv("TOKEN", "abc123")
        result = _resolve_env("Bearer ${TOKEN}")
        assert result == "Bearer abc123"

    def test_no_placeholders_returns_as_is(self):
        assert _resolve_env("plain text") == "plain text"


class TestLLMConfig:
    """Test LLM configuration with API key resolution."""

    def test_default_provider_and_model(self):
        llm = LLMConfig()
        assert llm.provider == "openai"
        assert llm.model == "gpt-4"

    def test_env_var_in_api_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
        llm = LLMConfig(api_key="${OPENAI_API_KEY}")
        assert llm.api_key == "sk-test-key"

    def test_provider_specific_key_takes_priority(self):
        llm = LLMConfig(
            provider="openai",
            api_key="generic-key",
            openai_api_key="specific-key",
        )
        assert llm.get_api_key() == "specific-key"

    def test_get_api_key_falls_back_to_generic(self):
        llm = LLMConfig(
            provider="openai",
            api_key="generic-key",
        )
        assert llm.get_api_key() == "generic-key"

    def test_get_api_key_anthropic(self):
        llm = LLMConfig(
            provider="anthropic",
            anthropic_api_key="anthropic-key",
        )
        assert llm.get_api_key() == "anthropic-key"

    def test_get_api_key_deepseek(self):
        llm = LLMConfig(
            provider="deepseek",
            deepseek_api_key="ds-key",
        )
        assert llm.get_api_key() == "ds-key"

    def test_repr_masks_api_key(self):
        llm = LLMConfig(api_key="secret")
        repr_str = repr(llm)
        assert "***" in repr_str
        assert "secret" not in repr_str

    def test_temperature_default(self):
        llm = LLMConfig()
        assert llm.temperature == 0.1


class TestRuleConfig:
    """Test RuleConfig validation."""

    def test_valid_rule(self):
        rule = RuleConfig(pattern="AssertionError", category="assertion", severity="medium")
        assert rule.pattern == "AssertionError"
        assert rule.category == "assertion"
        assert rule.severity.value == "medium"  # stored as ErrorSeverity enum

    def test_invalid_severity_raises(self):
        with pytest.raises(ValueError, match="Invalid severity"):
            RuleConfig(pattern=".*", category="test", severity="extreme")

    def test_invalid_pattern_raises(self):
        with pytest.raises(ValueError, match="Invalid regex pattern"):
            RuleConfig(pattern="[unclosed", category="test")

    def test_category_normalized(self):
        rule = RuleConfig(pattern=".*", category="  RUNtime  ")
        assert rule.category == "runtime"

    def test_severity_case_insensitive(self):
        rule = RuleConfig(pattern=".*", category="test", severity="LOW")
        assert rule.severity.value == "low"


class TestSaveLoadRoundtrip:
    """Test config save and load cycle."""

    def test_roundtrip_yaml(self):
        cfg = Config(
            watcher=WatcherConfig(type="pytest", path="my_tests/"),
            classifier=ClassifierConfig(type="rule"),
            engine=EngineConfig(max_retries=5, auto_apply=False),
            store=StoreConfig(type="sqlite"),
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            cfg.to_file(Path(f.name))
            f.flush()
            loaded = Config.from_file(Path(f.name))

        assert loaded.watcher.type == "pytest"
        assert loaded.watcher.path == "my_tests/"
        assert loaded.engine.max_retries == 5
        assert loaded.engine.auto_apply is False

    def test_load_nonexistent_file_returns_default(self):
        cfg = Config.from_file(Path("/nonexistent/path/config.yaml"))
        assert cfg.watcher.type == "pytest"
        assert cfg.engine.max_retries == 3

    def test_load_empty_yaml_returns_defaults(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("{}")
        cfg = Config.from_file(Path(f.name))
        assert cfg.watcher.type == "pytest"

    def test_to_file_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "subdir" / "config.yaml"
            cfg = Config()
            cfg.to_file(target)
            assert target.exists()
            data = yaml.safe_load(target.read_text())
            assert "watcher" in data

    def test_multi_watcher_roundtrip(self):
        cfg = Config(
            watcher=WatcherConfig(
                watchers=[
                    WatcherItemConfig(type="pytest", path="unit/"),
                    WatcherItemConfig(type="raw_log", path="logs/"),
                ]
            )
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            cfg.to_file(Path(f.name))
            loaded = Config.from_file(Path(f.name))
        items = loaded.watcher.get_watchers()
        assert len(items) == 2
        assert items[0].type == "pytest"
        assert items[1].type == "raw_log"

    def test_multi_reporter_roundtrip(self):
        cfg = Config(
            reporter=ReporterConfig(
                reporters=[
                    ReporterItemConfig(type="terminal"),
                    ReporterItemConfig(type="webhook", webhook_url="https://example.com/webhook"),
                ]
            )
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            cfg.to_file(Path(f.name))
            loaded = Config.from_file(Path(f.name))
        items = loaded.reporter.get_reporters()
        assert len(items) == 2
        assert items[0].type == "terminal"
        assert items[1].type == "webhook"


class TestConfigDefaults:
    """Test default config values."""

    def test_default_config_is_constructable(self):
        cfg = Config()
        assert cfg.watcher.type == "pytest"
        assert cfg.classifier.type == "rule"
        assert cfg.patcher.type == "template"
        assert cfg.validator.type == "local"
        assert cfg.reporter.type == "terminal"
        assert cfg.store.type == "memory"
        assert cfg.engine.max_retries == 3
        assert cfg.engine.auto_apply is False
        assert cfg.engine.dry_run is False

    def test_default_pipeline_stages(self):
        cfg = Config()
        pipeline = cfg.get_effective_pipeline()
        stages = [s.type for s in pipeline.stages if s.enabled]
        assert "classify" in stages
        assert "patch" in stages
        assert "validate" in stages
        assert "report" in stages
        assert "store" in stages

    def test_custom_pipeline_overrides_default(self):
        cfg = Config(
            pipeline=PipelineConfig(
                stages=[
                    PipelineStageConfig(type="classify"),
                    PipelineStageConfig(type="patch", retry=5),
                ]
            )
        )
        pipeline = cfg.get_effective_pipeline()
        stages = [s.type for s in pipeline.stages]
        assert stages == ["classify", "patch"]

    def test_disabled_stage_is_filtered(self):
        cfg = Config(
            pipeline=PipelineConfig(
                stages=[
                    PipelineStageConfig(type="classify"),
                    PipelineStageConfig(type="report", enabled=False),
                    PipelineStageConfig(type="store"),
                ]
            )
        )
        # Disabled stages are still in config but engine filters them
        pipeline = cfg.get_effective_pipeline()
        enabled = [s.type for s in pipeline.stages if s.enabled]
        assert "report" not in enabled


class TestPipelineStageConfigValidation:
    """Test PipelineStageConfig severity threshold validation."""

    def test_valid_thresholds(self):
        for val in ("low", "medium", "high", "critical"):
            cfg = PipelineStageConfig(type="patch", skip_if_severity_below=val)
            assert cfg.skip_if_severity_below == val

    def test_invalid_threshold(self):
        with pytest.raises(ValueError, match="Invalid severity threshold"):
            PipelineStageConfig(type="patch", skip_if_severity_below="extreme")

    def test_none_threshold_is_default(self):
        cfg = PipelineStageConfig(type="patch")
        assert cfg.skip_if_severity_below is None

    def test_case_insensitive(self):
        cfg = PipelineStageConfig(type="patch", skip_if_severity_below="HIGH")
        assert cfg.skip_if_severity_below == "high"


class TestMultiWatcher:
    """Test WatcherConfig multi-watcher support."""

    def test_no_watchers_falls_back_to_legacy(self):
        """Empty watchers list falls back to legacy single-watcher mode."""
        cfg = WatcherConfig(watchers=[])
        items = cfg.get_watchers()
        assert len(items) == 1  # fallback to legacy type+path

    def test_filter_disabled_watchers(self):
        cfg = WatcherConfig(
            watchers=[
                WatcherItemConfig(type="pytest", path="a/"),
                WatcherItemConfig(type="raw_log", path="b/", enabled=False),
                WatcherItemConfig(type="pytest", path="c/"),
            ]
        )
        items = cfg.get_watchers()
        assert len(items) == 2
        assert all(w.enabled for w in items)

    def test_watcher_item_defaults(self):
        item = WatcherItemConfig()
        assert item.type == "pytest"
        assert item.path == "tests/"
        assert item.poll_interval == 5.0
        assert item.enabled is True


class TestMultiReporter:
    """Test ReporterConfig multi-reporter support."""

    def test_single_reporter_legacy(self):
        cfg = ReporterConfig(type="terminal")
        items = cfg.get_reporters()
        assert len(items) == 1
        assert items[0].type == "terminal"

    def test_webhook_reporter_default_events(self):
        item = ReporterItemConfig(type="webhook")
        assert "passed" in item.webhook_events
        assert "failed" in item.webhook_events
        assert "error" in item.webhook_events
