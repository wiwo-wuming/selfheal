"""Integration tests for the full self-healing engine pipeline."""

import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from selfheal.config import Config, EngineConfig
from selfheal.engine import SelfHealEngine
from selfheal.events import (
    ClassificationEvent,
    ErrorSeverity,
    PatchEvent,
    TestFailureEvent,
    ValidationEvent,
)
from conftest import make_failure, create_mock_engine


@pytest.fixture
def temp_project():
    """Create a temporary project with a test file and source file."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Create a simple source file
        src = root / "calculator.py"
        src.write_text("def add(a, b):\n    return a + b\n")

        # Create a test file
        tests = root / "tests"
        tests.mkdir()
        test_file = tests / "test_calculator.py"
        test_file.write_text(
            "from calculator import add\n\n"
            "def test_add():\n"
            "    assert add(1, 2) == 3\n"
            "def test_add_fail():\n"
            "    assert add(1, 1) == 3  # intentional failure\n"
        )

        # Return paths
        yield {
            "root": root,
            "source": src,
            "test": test_file,
        }


class TestEngineRetry:
    """Test the engine's retry and apply logic."""

    def test_retry_on_failed_validation(self, temp_project):
        """Engine should retry when validation fails."""
        config = Config(
            engine=EngineConfig(max_retries=3, retry_delay=0, auto_apply=False,
                                strategy_fallback=False),
        )
        engine = create_mock_engine(config)
        engine.classifier = MagicMock()
        engine.classifier.classify.return_value = ClassificationEvent(
            original_event=make_failure(),
            category="assertion",
            severity=ErrorSeverity.MEDIUM,
            confidence=0.8,
        )
        engine.patcher = MagicMock()
        engine.patcher.generate.return_value = PatchEvent(
            classification_event=ClassificationEvent(
                original_event=make_failure(),
                category="assertion",
                severity=ErrorSeverity.MEDIUM,
                confidence=0.8,
            ),
            patch_id=str(uuid.uuid4()),
            patch_content="# fix",
            generator="template",
        )
        engine.validator = MagicMock()
        engine.validator.validate.return_value = ValidationEvent(
            patch_event=PatchEvent(
                classification_event=ClassificationEvent(
                    original_event=make_failure(),
                    category="assertion",
                    severity=ErrorSeverity.MEDIUM,
                    confidence=0.8,
                ),
                patch_id=str(uuid.uuid4()),
                patch_content="# fix",
                generator="template",
            ),
            result="failed",
            error_message="test still fails",
        )
        engine.reporter = MagicMock()
        engine.store = MagicMock()

        # Reset mocks to clear call counts from setup
        engine.patcher.generate.reset_mock()

        result = engine.process_failure(make_failure())

        # Should have retried max_retries times
        assert engine.patcher.generate.call_count == 3
        assert engine.metrics.total_retries == 2
        assert result.result == "failed"

    def test_stop_retry_on_pass(self):
        """Engine should stop retrying once validation passes."""
        config = Config(
            engine=EngineConfig(max_retries=5, retry_delay=0),
        )
        engine = create_mock_engine(config)
        engine.classifier = MagicMock()
        engine.classifier.classify.return_value = ClassificationEvent(
            original_event=make_failure(),
            category="assertion",
            severity=ErrorSeverity.MEDIUM,
            confidence=0.8,
        )
        engine.patcher = MagicMock()
        engine.patcher.generate.return_value = PatchEvent(
            classification_event=engine.classifier.classify(make_failure()),
            patch_id=str(uuid.uuid4()),
            patch_content="# fix",
            generator="template",
        )
        # Validation passes on 3rd try
        call_count = {"n": 0}

        def validate_side_effect(patch):
            call_count["n"] += 1
            if call_count["n"] >= 3:
                return ValidationEvent(
                    patch_event=patch,
                    result="passed",
                )
            return ValidationEvent(
                patch_event=patch,
                result="failed",
            )

        engine.validator = MagicMock()
        engine.validator.validate.side_effect = validate_side_effect
        engine.reporter = MagicMock()
        engine.store = MagicMock()

        result = engine.process_failure(make_failure())

        assert result.result == "passed"
        # With separated PatchStage + ValidateStage, PatchStage always
        # generates max_retries patches; ValidateStage picks the first pass.
        assert engine.patcher.generate.call_count == 5

    def test_target_file_resolution(self, temp_project):
        """Engine should resolve the target source file from test path."""
        config = Config()
        engine = create_mock_engine(config)
        # tests/test_calculator.py -> calculator.py
        resolved = engine._resolve_target_file(
            str(temp_project["test"])
        )
        assert resolved is not None
        assert Path(resolved).name == "calculator.py"

    def test_target_file_resolution_unknown(self):
        """Should return None for unresolvable test paths."""
        config = Config()
        engine = create_mock_engine(config)
        resolved = engine._resolve_target_file("totally/unknown/path.py::test_x")
        assert resolved is None

    def test_process_batch(self):
        """Batch processing should handle multiple failures."""
        config = Config(
            engine=EngineConfig(max_retries=1, retry_delay=0),
        )
        engine = create_mock_engine(config)

        def classify(e):
            return ClassificationEvent(
                original_event=e,
                category="assertion",
                severity=ErrorSeverity.MEDIUM,
                confidence=0.8,
            )

        def generate(c):
            return PatchEvent(
                classification_event=c,
                patch_id=str(uuid.uuid4()),
                patch_content="# fix",
                generator="template",
            )

        def validate(p):
            return ValidationEvent(patch_event=p, result="passed")

        engine.classifier = MagicMock()
        engine.classifier.classify.side_effect = classify
        engine.patcher = MagicMock()
        engine.patcher.generate.side_effect = generate
        engine.validator = MagicMock()
        engine.validator.validate.side_effect = validate
        engine.reporter = MagicMock()
        engine.store = MagicMock()

        failures = [make_failure(f"tests/test_{i}.py::test_x") for i in range(5)]
        results = engine.process_batch(failures)

        assert len(results) == 5
        assert all(r.result == "passed" for r in results)
