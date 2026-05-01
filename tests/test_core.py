"""Tests for core SelfHeal functionality."""

import pytest
from datetime import datetime

from selfheal.events import (
    TestFailureEvent,
    ClassificationEvent,
    PatchEvent,
    ErrorSeverity,
)
from selfheal.config import Config, WatcherConfig, ClassifierConfig
from selfheal.core.classifiers.rule_classifier import RuleClassifier


class TestEvents:
    """Test event classes."""

    def test_test_failure_event_creation(self):
        """Test creating a TestFailureEvent."""
        event = TestFailureEvent(
            test_path="tests/test_example.py::test_case",
            error_type="AssertionError",
            error_message="Expected 1, got 2",
            traceback="...",
        )

        assert event.test_path == "tests/test_example.py::test_case"
        assert event.error_type == "AssertionError"
        assert event.error_message == "Expected 1, got 2"
        assert event.traceback == "..."
        assert isinstance(event.timestamp, datetime)

    def test_test_failure_event_to_dict(self):
        """Test converting event to dictionary."""
        event = TestFailureEvent(
            test_path="tests/test_example.py::test_case",
            error_type="AssertionError",
            error_message="Expected 1, got 2",
        )

        data = event.to_dict()

        assert data["test_path"] == "tests/test_example.py::test_case"
        assert data["error_type"] == "AssertionError"
        assert data["error_message"] == "Expected 1, got 2"
        assert "timestamp" in data

    def test_classification_event_creation(self):
        """Test creating a ClassificationEvent."""
        failure = TestFailureEvent(
            test_path="tests/test_example.py",
            error_type="ImportError",
            error_message="No module named 'requests'",
        )

        classification = ClassificationEvent(
            original_event=failure,
            category="import",
            severity=ErrorSeverity.HIGH,
            confidence=0.95,
            reasoning="Matched ImportError pattern",
        )

        assert classification.category == "import"
        assert classification.severity == ErrorSeverity.HIGH
        assert classification.confidence == 0.95

    def test_patch_event_creation(self):
        """Test creating a PatchEvent."""
        failure = TestFailureEvent(
            test_path="tests/test_example.py",
            error_type="AssertionError",
            error_message="Test failed",
        )

        classification = ClassificationEvent(
            original_event=failure,
            category="assertion",
            severity=ErrorSeverity.MEDIUM,
            confidence=0.8,
        )

        patch = PatchEvent(
            classification_event=classification,
            patch_id="patch-123",
            patch_content="# Fix for assertion error",
            generator="template",
        )

        assert patch.patch_id == "patch-123"
        assert patch.generator == "template"
        assert patch.status == "generated"


class TestRuleClassifier:
    """Test RuleClassifier."""

    def test_classifier_creation(self):
        """Test creating a RuleClassifier."""
        config = ClassifierConfig()
        classifier = RuleClassifier(config)

        assert classifier.name == "rule"

    def test_classify_assertion_error(self):
        """Test classifying an AssertionError."""
        config = ClassifierConfig()
        classifier = RuleClassifier(config)

        event = TestFailureEvent(
            test_path="tests/test_example.py::test_case",
            error_type="AssertionError",
            error_message="Expected True, got False",
        )

        result = classifier.classify(event)

        assert result.category == "assertion"
        assert result.severity == ErrorSeverity.MEDIUM
        assert result.confidence > 0.5

    def test_classify_import_error(self):
        """Test classifying an ImportError."""
        config = ClassifierConfig()
        classifier = RuleClassifier(config)

        event = TestFailureEvent(
            test_path="tests/test_example.py",
            error_type="ModuleNotFoundError",
            error_message="No module named 'requests'",
        )

        result = classifier.classify(event)

        assert result.category == "import"
        assert result.severity == ErrorSeverity.HIGH

    def test_classify_timeout_error(self):
        """Test classifying a TimeoutError."""
        config = ClassifierConfig()
        classifier = RuleClassifier(config)

        event = TestFailureEvent(
            test_path="tests/test_example.py::test_slow",
            error_type="TimeoutError",
            error_message="Operation timed out after 30 seconds",
        )

        result = classifier.classify(event)

        assert result.category == "timeout"

    def test_classify_unknown_error(self):
        """Test classifying an unknown error."""
        config = ClassifierConfig()
        classifier = RuleClassifier(config)

        event = TestFailureEvent(
            test_path="tests/test_example.py",
            error_type="CustomError",
            error_message="Something went wrong",
        )

        result = classifier.classify(event)

        assert result.category == "unknown"
        assert result.confidence < 0.5


class TestConfig:
    """Test configuration."""

    def test_default_config(self):
        """Test creating default config."""
        config = Config()

        assert config.watcher.type == "pytest"
        assert config.classifier.type == "rule"
        assert config.patcher.type == "template"
        assert config.validator.type == "local"
        assert config.store.type == "memory"

    def test_watcher_config(self):
        """Test watcher config."""
        config = WatcherConfig(
            type="pytest",
            path="tests/",
            pytest_args=["-v", "--tb=short"],
        )

        assert config.type == "pytest"
        assert config.path == "tests/"
        assert "-v" in config.pytest_args
