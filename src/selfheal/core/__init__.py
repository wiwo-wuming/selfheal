"""Core components for SelfHeal."""

from selfheal.core.watchers import PytestWatcher, RawLogWatcher, PluginWatcher
from selfheal.core.classifiers import RuleClassifier, LLMClassifier, HybridClassifier
from selfheal.core.patchers import TemplatePatcher, LLMPatcher
from selfheal.core.validators import LocalValidator, DockerValidator
from selfheal.core.reporters import TerminalReporter, GitHubReporter, WebhookReporter
from selfheal.core.stores import MemoryStore, SQLiteStore
from selfheal.core.applier import PatchApplier
from selfheal.core.metrics import MetricsCollector
from selfheal.core.pipeline_stages import (
    ClassifyStage,
    PatchStage,
    ValidateStage,
    ReportStage,
    StoreStage,
)
from selfheal.registry import get_registry

__all__ = [
    "PytestWatcher",
    "RawLogWatcher",
    "PluginWatcher",
    "RuleClassifier",
    "LLMClassifier",
    "HybridClassifier",
    "TemplatePatcher",
    "LLMPatcher",
    "LocalValidator",
    "DockerValidator",
    "TerminalReporter",
    "GitHubReporter",
    "WebhookReporter",
    "MemoryStore",
    "SQLiteStore",
    "PatchApplier",
    "MetricsCollector",
    "ClassifyStage",
    "PatchStage",
    "ValidateStage",
    "ReportStage",
    "StoreStage",
]


def register_defaults() -> None:
    """Register all default core components into the global registry."""
    registry = get_registry()

    registry.register_watcher("pytest", PytestWatcher)
    registry.register_watcher("raw_log", RawLogWatcher)
    registry.register_watcher("plugin_watcher", PluginWatcher)

    registry.register_classifier("rule", RuleClassifier)
    registry.register_classifier("llm", LLMClassifier)
    registry.register_classifier("hybrid", HybridClassifier)

    registry.register_patcher("template", TemplatePatcher)
    registry.register_patcher("llm", LLMPatcher)

    registry.register_validator("local", LocalValidator)
    registry.register_validator("docker", DockerValidator)

    registry.register_reporter("terminal", TerminalReporter)
    registry.register_reporter("github", GitHubReporter)
    registry.register_reporter("webhook", WebhookReporter)

    registry.register_store("memory", MemoryStore)
    registry.register_store("sqlite", SQLiteStore)

    registry.register_stage("classify", ClassifyStage)
    registry.register_stage("patch", PatchStage)
    registry.register_stage("validate", ValidateStage)
    registry.register_stage("report", ReportStage)
    registry.register_stage("store", StoreStage)


# Auto-register defaults on import
register_defaults()
