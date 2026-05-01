"""Configuration management for SelfHeal."""

import logging
import os
import re
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


def _resolve_env(value: str) -> str:
    """Resolve ${ENV_VAR} references in config values."""
    pattern = re.compile(r"\$\{([^}]+)\}")
    return pattern.sub(lambda m: os.environ.get(m.group(1), ""), value)


class RuleConfig(BaseModel):
    """Single classification rule with validation.

    Ensures typos in severity/category/pattern are caught at config-load time
    rather than silently producing wrong behaviour at runtime.
    """

    pattern: str = Field(..., description="Regular expression to match against error type/message")
    category: str = Field(..., description="Error category, e.g. assertion, import, runtime")
    severity: str = Field(default="medium", description="Severity level")

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        allowed = {"critical", "high", "medium", "low"}
        normalized = v.strip().lower()
        if normalized not in allowed:
            raise ValueError(
                f"Invalid severity '{v}'. Must be one of: {', '.join(sorted(allowed))}"
            )
        return normalized

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("pattern")
    @classmethod
    def validate_pattern(cls, v: str) -> str:
        try:
            re.compile(v)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern '{v}': {e}") from e
        return v


class LLMConfig(BaseModel):
    """LLM provider configuration."""
    provider: str = "openai"
    model: str = "gpt-4"
    api_key: Optional[str] = None
    base_url: Optional[str] = None

    @field_validator("api_key", "base_url", mode="before")
    @classmethod
    def resolve_env_vars(cls, v: Optional[str]) -> Optional[str]:
        """Resolve environment variable placeholders like ${OPENAI_API_KEY}."""
        if v and isinstance(v, str) and "${" in v:
            return _resolve_env(v)
        return v


class DockerConfig(BaseModel):
    """Docker configuration."""
    image: str = "python:3.11-slim"
    timeout: int = 600
    network: Optional[str] = None


class GitHubConfig(BaseModel):
    """GitHub integration configuration."""
    owner: str = ""
    repo: str = ""
    token: Optional[str] = None
    labels: list[str] = Field(default_factory=lambda: ["self-heal", "automated"])


class WatcherItemConfig(BaseModel):
    """Configuration for a single watcher instance."""
    type: str = "pytest"
    path: str = "tests/"
    pytest_args: list[str] = Field(default_factory=lambda: ["-v", "--tb=short"])
    poll_interval: float = 5.0  # seconds between watch cycles
    watch_patterns: list[str] = Field(default_factory=lambda: ["*.py"])
    enabled: bool = True


class WatcherConfig(BaseModel):
    """Watcher configuration — supports both single and multi-watcher modes.

    Single-watcher (backward compat):
        watcher:
          type: pytest
          path: tests/

    Multi-watcher:
        watcher:
          watchers:
            - type: pytest
              path: tests/
            - type: raw_log
              path: logs/
              watch_patterns:
                - "*.log"
    """
    type: str = "pytest"  # used only in single-watcher mode
    path: str = "tests/"
    pytest_args: list[str] = Field(default_factory=lambda: ["-v", "--tb=short"])
    poll_interval: float = 5.0
    watch_patterns: list[str] = Field(default_factory=lambda: ["*.py"])
    watchers: list[WatcherItemConfig] = Field(default_factory=list)

    def get_watchers(self) -> list[WatcherItemConfig]:
        """Return the effective list of watcher items.

        If the ``watchers`` list is populated, use it (multi-watcher mode).
        Otherwise build a single-item list from the legacy fields.
        """
        if self.watchers:
            return [w for w in self.watchers if w.enabled]
        return [WatcherItemConfig(
            type=self.type,
            path=self.path,
            pytest_args=self.pytest_args,
            poll_interval=self.poll_interval,
            watch_patterns=self.watch_patterns,
        )]


class ClassifierConfig(BaseModel):
    """Classifier configuration."""
    type: str = "rule"
    rules: list[RuleConfig] = Field(default_factory=list)
    llm: Optional[LLMConfig] = None


class PatcherConfig(BaseModel):
    """Patcher configuration."""
    type: str = "template"
    templates_dir: str = "patches/"
    llm: Optional[LLMConfig] = None


class ValidatorConfig(BaseModel):
    """Validator configuration."""
    type: str = "local"
    timeout: int = 300
    venv_path: Optional[str] = None
    docker: Optional[DockerConfig] = None


class ReporterItemConfig(BaseModel):
    """Configuration for a single reporter in a chain."""
    type: str = "terminal"
    enabled: bool = True
    github: Optional[GitHubConfig] = None
    webhook_url: Optional[str] = None
    webhook_events: list[str] = Field(default_factory=lambda: ["passed", "failed", "error"])


class ReporterConfig(BaseModel):
    """Reporter configuration — supports both single and multi-reporter modes.

    Single-reporter (backward compat):
        reporter:
          type: terminal

    Multi-reporter chain:
        reporter:
          reporters:
            - type: terminal
            - type: webhook
              webhook_url: https://hooks.example.com/...
    """
    type: str = "terminal"  # used only in single-reporter mode
    github: Optional[GitHubConfig] = None
    webhook_url: Optional[str] = None
    webhook_events: list[str] = Field(default_factory=lambda: ["passed", "failed", "error"])
    reporters: list[ReporterItemConfig] = Field(default_factory=list)

    def get_reporters(self) -> list[ReporterItemConfig]:
        """Return the effective list of reporter items.

        If the ``reporters`` list is populated, use it (multi-reporter mode).
        Otherwise build a single-item list from the legacy ``type`` field.
        """
        if self.reporters:
            return [r for r in self.reporters if r.enabled]
        return [ReporterItemConfig(
            type=self.type,
            github=self.github,
            webhook_url=self.webhook_url,
            webhook_events=self.webhook_events,
        )]


class PipelineStageConfig(BaseModel):
    """Configuration for a single pipeline stage."""
    type: str = Field(..., description="Stage name: classify, patch, validate, report, store")
    enabled: bool = True
    retry: int = 1  # max retries for this stage (used by patch stage)


class PipelineConfig(BaseModel):
    """Pluggable pipeline configuration.

    If not provided in the YAML config, the engine falls back to the
    default 4-stage pipeline (classify → patch → report → store).
    """
    stages: list[PipelineStageConfig] = Field(default_factory=lambda: [
        PipelineStageConfig(type="classify"),
        PipelineStageConfig(type="patch", retry=3),
        PipelineStageConfig(type="validate"),
        PipelineStageConfig(type="report"),
        PipelineStageConfig(type="store"),
    ])


class PluginConfig(BaseModel):
    """Plugin hot-reloading configuration."""
    enabled: bool = False  # enable PluginWatcher integration
    plugin_dir: str = "plugins/"  # directory containing plugin .py files
    check_integrity_on_start: bool = True  # verify plugin integrity when engine starts
    check_integrity_on_failure: bool = True  # verify plugin integrity before processing a failure
    fail_on_integrity_violation: bool = False  # if True, integrity violation stops processing


class EngineConfig(BaseModel):
    """Engine behaviour configuration."""
    max_retries: int = 3
    retry_delay: float = 1.0  # seconds between retries
    auto_apply: bool = False  # if False, patches are generated but not applied
    backup_dir: str = ".selfheal/backups"
    strategy_fallback: bool = True  # try alternative patcher on failure


class StoreConfig(BaseModel):
    """Store configuration."""
    type: str = "memory"
    db_path: str = ".selfheal/selfheal.db"


class Config(BaseModel):
    """Main configuration."""
    llm: Optional[LLMConfig] = None
    docker: Optional[DockerConfig] = None
    github: Optional[GitHubConfig] = None
    watcher: WatcherConfig = Field(default_factory=WatcherConfig)
    classifier: ClassifierConfig = Field(default_factory=ClassifierConfig)
    patcher: PatcherConfig = Field(default_factory=PatcherConfig)
    validator: ValidatorConfig = Field(default_factory=ValidatorConfig)
    reporter: ReporterConfig = Field(default_factory=ReporterConfig)
    store: StoreConfig = Field(default_factory=StoreConfig)
    engine: EngineConfig = Field(default_factory=EngineConfig)
    pipeline: Optional[PipelineConfig] = None
    plugin: PluginConfig = Field(default_factory=PluginConfig)

    def get_effective_pipeline(self) -> PipelineConfig:
        """Return the effective pipeline configuration.

        If ``pipeline`` is explicitly configured, use it.  Otherwise return
        the default 4-stage pipeline (classify → patch → report → store).

        This ensures backward compatibility: existing configs without a
        ``pipeline`` section continue to work.
        """
        if self.pipeline is not None:
            return self.pipeline
        return PipelineConfig()

    @classmethod
    def from_file(cls, path: Path) -> "Config":
        """Load configuration from YAML file."""
        if not path.exists():
            return cls()

        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(**data)

    @classmethod
    def load_default(cls) -> "Config":
        """Load configuration from default locations."""
        # Check common locations
        locations = [
            Path.cwd() / "selfheal.yaml",
            Path.cwd() / ".selfheal.yaml",
            Path.home() / ".selfheal.yaml",
        ]

        for loc in locations:
            if loc.exists():
                return cls.from_file(loc)

        return cls()

    def to_file(self, path: Path) -> None:
        """Save configuration to YAML file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(self.model_dump(exclude_none=True), f, default_flow_style=False)
