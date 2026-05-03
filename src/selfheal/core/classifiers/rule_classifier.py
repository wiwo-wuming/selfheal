"""Rule-based classifier implementation."""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

from selfheal.config import ClassifierConfig, RuleConfig
from selfheal.events import (
    TestFailureEvent,
    ClassificationEvent,
    ErrorSeverity,
    ErrorCategory,
)
from selfheal.interfaces.classifier import ClassifierInterface


# Default classification rules (kept as dicts for readability; converted at compile time)
DEFAULT_RULES = [
    {"pattern": "AssertionError", "category": ErrorCategory.ASSERTION, "severity": "medium"},
    {"pattern": "ImportError", "category": ErrorCategory.IMPORT, "severity": "high"},
    {"pattern": "ModuleNotFoundError", "category": ErrorCategory.IMPORT, "severity": "high"},
    {"pattern": "TimeoutError", "category": ErrorCategory.TIMEOUT, "severity": "medium"},
    {"pattern": "ConnectionError", "category": ErrorCategory.NETWORK, "severity": "high"},
    {"pattern": "SyntaxError", "category": ErrorCategory.SYNTAX, "severity": "critical"},
    {"pattern": "IndentationError", "category": ErrorCategory.SYNTAX, "severity": "high"},
    {"pattern": "NameError", "category": ErrorCategory.RUNTIME, "severity": "medium"},
    {"pattern": "TypeError", "category": ErrorCategory.TYPE, "severity": "medium"},
    {"pattern": "ValueError", "category": ErrorCategory.VALUE, "severity": "medium"},
    {"pattern": "KeyError", "category": ErrorCategory.RUNTIME, "severity": "medium"},
    {"pattern": "AttributeError", "category": ErrorCategory.RUNTIME, "severity": "medium"},
    {"pattern": "ZeroDivisionError", "category": ErrorCategory.RUNTIME, "severity": "medium"},
    {"pattern": "FileNotFoundError", "category": ErrorCategory.RESOURCE, "severity": "medium"},
    {"pattern": "PermissionError", "category": ErrorCategory.PERMISSION, "severity": "high"},
    {"pattern": "MemoryError", "category": ErrorCategory.MEMORY, "severity": "critical"},
    {"pattern": "ConfigError|ConfigurationError|MissingEnv", "category": ErrorCategory.CONFIG, "severity": "high"},
    {"pattern": "pip install|version conflict|incompatible.*version|dependency", "category": ErrorCategory.DEPENDENCY, "severity": "high"},
    {"pattern": "DatabaseError|OperationalError|sqlite3.OperationalError", "category": ErrorCategory.RESOURCE, "severity": "high"},
    {"pattern": "NotADirectoryError|FileExistsError|IsADirectoryError", "category": ErrorCategory.RESOURCE, "severity": "medium"},
    {"pattern": "flaky|intermittent|race condition|deadlock", "category": ErrorCategory.FLAKY, "severity": "low"},
    {"pattern": "OSError", "category": ErrorCategory.RESOURCE, "severity": "medium"},
    {"pattern": "BrokenPipeError|ConnectionResetError|ConnectionRefusedError", "category": ErrorCategory.NETWORK, "severity": "high"},
    {"pattern": "RecursionError", "category": ErrorCategory.RUNTIME, "severity": "high"},
    {"pattern": "NotImplementedError", "category": ErrorCategory.RUNTIME, "severity": "low"},
    {"pattern": "OverflowError", "category": ErrorCategory.VALUE, "severity": "medium"},
]


class RuleClassifier(ClassifierInterface):
    """Rule-based error classifier."""

    def __init__(self, config: ClassifierConfig):
        self.config = config
        self._rules = self._compile_rules()

    name = "rule"

    def _compile_rules(self) -> list[dict[str, object]]:
        """Compile rules from config or use defaults.

        Supports both RuleConfig objects (from typed config) and plain dicts
        (from DEFAULT_RULES / legacy config) for backward compatibility.
        """
        rules = self.config.rules if self.config.rules else DEFAULT_RULES
        compiled: list[dict[str, object]] = []
        for rule in rules:
            if isinstance(rule, RuleConfig):
                compiled.append({
                    "pattern": re.compile(rule.pattern),
                    "category": rule.category,
                    "severity": rule.severity,  # already ErrorSeverity via Pydantic validator
                })
            else:
                # Legacy dict format — category may be ErrorCategory enum or str
                category: object = rule["category"]
                if isinstance(category, ErrorCategory):
                    category = category.value
                pattern: object = rule["pattern"]
                if isinstance(pattern, str):
                    pattern = re.compile(pattern)
                compiled.append({
                    "pattern": pattern,
                    "category": category,
                    "severity": ErrorSeverity(str(rule.get("severity", "medium"))),
                })
        return compiled

    def classify(self, event: TestFailureEvent) -> ClassificationEvent:
        """Classify a test failure using rules."""
        # Try to match error type
        error_type = event.error_type
        error_message = event.error_message
        traceback = event.traceback

        best_match: Optional[dict[str, object]] = None
        best_confidence = 0.0

        # Check error type first (highest priority)
        for rule in self._rules:
            if rule["pattern"].search(error_type):
                best_match = rule
                best_confidence = 0.9
                break

        # Check error message
        if not best_match:
            for rule in self._rules:
                if rule["pattern"].search(error_message):
                    best_match = rule
                    best_confidence = 0.7
                    break

        # Check traceback
        if not best_match:
            for rule in self._rules:
                if rule["pattern"].search(traceback):
                    best_match = rule
                    best_confidence = 0.5
                    break

        if best_match:
            return ClassificationEvent(
                original_event=event,
                category=best_match["category"],
                severity=best_match["severity"],
                confidence=best_confidence,
                reasoning=f"Matched pattern: {best_match['pattern'].pattern}",
            )

        # No match found
        logger.info(
            "No rule matched for error_type=%s, returning UNKNOWN classification",
            error_type,
        )
        return ClassificationEvent(
            original_event=event,
            category=ErrorCategory.UNKNOWN.value,
            severity=ErrorSeverity.MEDIUM,
            confidence=0.1,
            reasoning="No matching rule found",
        )
