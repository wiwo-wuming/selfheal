"""Tests for SelfHeal engine."""

import pytest
from unittest.mock import MagicMock, patch

from selfheal.config import Config
from selfheal.events import TestFailureEvent, ClassificationEvent, PatchEvent, ValidationEvent, ErrorSeverity
from selfheal.engine import SelfHealEngine


class TestSelfHealEngine:
    """Test the SelfHealEngine class."""

    def test_engine_creation(self):
        """Test creating an engine."""
        config = Config()
        engine = SelfHealEngine(config)

        assert engine.config == config
        assert engine.watcher is not None
        assert engine.classifier is not None
        assert engine.patcher is not None
        assert engine.validator is not None
        assert engine.reporter is not None
        assert engine.store is not None

    def test_process_failure_flow(self):
        """Test the full failure processing flow."""
        config = Config()
        engine = SelfHealEngine(config)

        # Create a test failure
        failure = TestFailureEvent(
            test_path="tests/test_example.py::test_case",
            error_type="AssertionError",
            error_message="Expected 1, got 2",
            traceback="...",
        )

        # Process the failure
        result = engine.process_failure(failure)

        # Verify the result
        assert isinstance(result, ValidationEvent)
        assert result.patch_event.classification_event.original_event == failure

    def test_engine_shutdown(self):
        """Test engine shutdown."""
        config = Config()
        engine = SelfHealEngine(config)

        # Should not raise
        engine.shutdown()


class TestEngineIntegration:
    """Integration tests for the engine."""

    def test_full_pipeline_with_mocked_components(self):
        """Test the full pipeline with mocked components."""
        config = Config()

        # Create engine
        engine = SelfHealEngine(config)

        # Create a test failure event
        failure = TestFailureEvent(
            test_path="tests/test_auth.py::test_login",
            error_type="AssertionError",
            error_message="Expected status 200, got 401",
            traceback="...",
        )

        # Process through pipeline
        result = engine.process_failure(failure)

        # Verify classification
        assert result.patch_event.classification_event.category in [
            "assertion",
            "unknown",
        ]

        # Verify patch was generated
        assert result.patch_event.patch_id is not None
        assert len(result.patch_event.patch_content) > 0

        # Verify validation result is one of expected values
        assert result.result in ["passed", "failed", "error"]
