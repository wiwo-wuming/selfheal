"""Rule-based classifier implementation."""

import re
from typing import Optional

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
    {"pattern": "TypeError", "category": ErrorCategory.RUNTIME, "severity": "medium"},
    {"pattern": "ValueError", "category": ErrorCategory.RUNTIME, "severity": "low"},
    {"pattern": "KeyError", "category": ErrorCategory.RUNTIME, "severity": "medium"},
    {"pattern": "AttributeError", "category": ErrorCategory.RUNTIME, "severity": "medium"},
    {"pattern": "ZeroDivisionError", "category": ErrorCategory.RUNTIME, "severity": "medium"},
    {"pattern": "FileNotFoundError", "category": ErrorCategory.RUNTIME, "severity": "medium"},
    {"pattern": "PermissionError", "category": ErrorCategory.RUNTIME, "severity": "high"},
    {"pattern": "MemoryError", "category": ErrorCategory.RUNTIME, "severity": "critical"},
]


class RuleClassifier(ClassifierInterface):
    """Rule-based error classifier."""

    def __init__(self, config: ClassifierConfig):
        self.config = config
        self._rules = self._compile_rules()

    name = "rule"

    def _compile_rules(self) -> list[dict]:
        """Compile rules from config or use defaults.

        Supports both RuleConfig objects (from typed config) and plain dicts
        (from DEFAULT_RULES / legacy config) for backward compatibility.
        """
        rules = self.config.rules if self.config.rules else DEFAULT_RULES
        compiled = []
        for rule in rules:
            if isinstance(rule, RuleConfig):
                compiled.append({
                    "pattern": re.compile(rule.pattern),
                    "category": rule.category,
                    "severity": ErrorSeverity(rule.severity),
                })
            else:
                # Legacy dict format — category may be ErrorCategory enum or str
                category = rule["category"]
                if isinstance(category, ErrorCategory):
                    category = category.value
                compiled.append({
                    "pattern": re.compile(rule["pattern"]),
                    "category": category,
                    "severity": ErrorSeverity(rule.get("severity", "medium")),
                })
        return compiled

    def classify(self, event: TestFailureEvent) -> ClassificationEvent:
        """Classify a test failure using rules."""
        # Try to match error type
        error_type = event.error_type
        error_message = event.error_message
        traceback = event.traceback

        best_match: Optional[dict] = None
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
        return ClassificationEvent(
            original_event=event,
            category=ErrorCategory.UNKNOWN.value,
            severity=ErrorSeverity.MEDIUM,
            confidence=0.1,
            reasoning="No matching rule found",
        )
