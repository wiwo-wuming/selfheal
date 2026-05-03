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
    """Resolve ${ENV_VAR} and ${ENV_VAR:-default} references in config values."""
    # Match ${VAR} or ${VAR:-default}
    pattern = re.compile(r"\$\{([^}:-]+)(?::-([^}]*))?\}")
    def _replacer(m: re.Match) -> str:
        name = m.group(1)
        default = m.group(2) if m.group(2) is not None else ""
        return os.environ.get(name, default).strip()
    return pattern.sub(_replacer, value)


class RuleConfig(BaseModel):
    """Single classification rule with validation.

    Ensures typos in severity/category/pattern are caught at config-load time
    rather than silently producing wrong behaviour at runtime.
    """

    pattern: str = Field(..., description="Regular expression to match against error type/message")
    category: str = Field(..., description="Error category, e.g. assertion, import, runtime")
    severity: object = Field(default="medium", description="Severity level (ErrorSeverity enum or string)")

    @field_validator("severity", mode="before")
    @classmethod
    def validate_severity(cls, v: object) -> object:
        from selfheal.events import ErrorSeverity

        if isinstance(v, ErrorSeverity):
            return v
        if isinstance(v, str):
            try:
                return ErrorSeverity(v.strip().lower())
            except ValueError:
                allowed = {s.value for s in ErrorSeverity}
                raise ValueError(
                    f"Invalid severity '{v}'. Must be one of: {', '.join(sorted(allowed))}"
                ) from None
        raise ValueError(f"Invalid severity type: {type(v)}")

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
    """LLM provider configuration.

    API keys are resolved in this priority order per provider:
      - OpenAI:     ``openai_api_key`` → ``${OPENAI_API_KEY}`` env → ``api_key``
      - Anthropic:  ``anthropic_api_key`` → ``${ANTHROPIC_API_KEY}`` env → ``api_key``
      - DeepSeek:   ``deepseek_api_key`` → ``${DEEPSEEK_API_KEY}`` env → ``api_key``
    """

    provider: str = "openai"
    model: str = "gpt-4"
    api_key: Optional[str] = None  # generic fallback (backward-compat)
    base_url: Optional[str] = None
    # Provider-specific API keys (preferred over generic api_key)
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    temperature: float = 0.1

    @field_validator(
        "api_key", "base_url",
        "openai_api_key", "anthropic_api_key", "deepseek_api_key",
        mode="before",
    )
    @classmethod
    def resolve_env_vars(cls, v: Optional[str]) -> Optional[str]:
        """Resolve environment variable placeholders like ${OPENAI_API_KEY}."""
        if v and isinstance(v, str) and "${" in v:
            return _resolve_env(v).strip()
        if v and isinstance(v, str):
            return v.strip()
        return v

    def __repr__(self) -> str:
        """Safe repr that masks API key values."""
        return (
            f"LLMConfig(provider={self.provider!r}, model={self.model!r}, "
            f"api_key={'***' if self.api_key else None}, "
            f"base_url={self.base_url!r}, temperature={self.temperature})"
        )

    def get_api_key(self) -> Optional[str]:
        """Return the effective API key for the configured provider.

        Priority: provider-specific key > generic api_key fallback.
        """
        provider_key_map = {
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "deepseek": self.deepseek_api_key,
        }
        specific = provider_key_map.get(self.provider.lower())
        if specific:
            return specific
        return self.api_key


class DockerConfig(BaseModel):
    """Docker configuration."""
    image: str = "python:3.11-slim"
    timeout: int = 600
    network: Optional[str] = None
    sandbox: bool = True  # if True, validate in temp copy (safe); if False, mount host dir RW (fast)


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
    # Hybrid classifier settings
    confidence_threshold: float = 0.5  # min rule confidence before falling back to LLM
    cache_enabled: bool = True  # enable LLM response cache
    cache_ttl: float = 3600.0  # cache TTL in seconds (default 1 hour)


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
    webhook_secret: Optional[str] = None  # HMAC-SHA256 shared secret for signing
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
    webhook_secret: Optional[str] = None  # HMAC-SHA256 shared secret for signing
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
            webhook_secret=self.webhook_secret,
            webhook_events=self.webhook_events,
        )]


class PipelineStageConfig(BaseModel):
    """Configuration for a single pipeline stage."""
    type: str = Field(..., description="Stage name: classify, patch, validate, report, store")
    enabled: bool = True
    retry: int = 1  # max retries for this stage (used by patch stage)
    skip_if_severity_below: Optional[str] = Field(
        default=None,
        description="Skip this stage if the classified severity is below this threshold. "
                    "One of: low, medium, high, critical. 'low' means everything runs; "
                    "'critical' means only critical failures go through this stage.",
    )

    @field_validator("skip_if_severity_below", mode="before")
    @classmethod
    def validate_severity_threshold(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        allowed = {"low", "medium", "high", "critical"}
        normalized = v.strip().lower()
        if normalized not in allowed:
            raise ValueError(
                f"Invalid severity threshold '{v}'. Must be one of: {', '.join(sorted(allowed))}"
            )
        return normalized


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
    dry_run: bool = False  # if True, preview patches without modifying files
    backup_dir: str = ".selfheal/backups"
    strategy_fallback: bool = True  # try alternative patcher on failure
    experience_enabled: bool = True  # record & reuse successful fixes from experience store
    experience_db_path: str = ".selfheal/experience.db"  # path to experience SQLite database
    max_concurrency: int = 1  # max concurrent pipeline runs (1 = sequential, >1 = async parallel)
    async_batch: bool = False  # if True, process_batch uses asyncio for parallel execution


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

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(**data)

    @classmethod
    def load_default(cls) -> "Config":
        """Load configuration from default locations."""
        cwd = Path.cwd()
        locations = [
            cwd / "selfheal.yaml",
            cwd / ".selfheal.yaml",
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
