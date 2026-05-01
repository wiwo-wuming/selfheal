"""Tests for ValidateStage pipeline stage."""

from unittest.mock import MagicMock

import pytest

from selfheal.core.pipeline_stages.validate_stage import ValidateStage
from selfheal.events import (
    ClassificationEvent,
    ErrorSeverity,
    PatchEvent,
    TestFailureEvent,
    ValidationEvent,
)


def make_failure(test_path="tests/test_calc.py::test_foo"):
    """Helper to create a TestFailureEvent."""
    return TestFailureEvent(
        test_path=test_path,
        error_type="AssertionError",
        error_message="assert 1 == 2",
        traceback="...",
    )


def make_classification(event=None):
    """Helper to create a ClassificationEvent."""
    return ClassificationEvent(
        original_event=event or make_failure(),
        category="assertion",
        severity=ErrorSeverity.MEDIUM,
        confidence=0.8,
    )


def make_patch(classification=None, patch_id="patch-1", generator="template"):
    """Helper to create a PatchEvent."""
    return PatchEvent(
        classification_event=classification or make_classification(),
        patch_id=patch_id,
        patch_content="# fix",
        generator=generator,
    )


def make_passed(patch):
    """Helper to create a 'passed' ValidationEvent."""
    return ValidationEvent(patch_event=patch, result="passed")


def make_failed(patch):
    """Helper to create a 'failed' ValidationEvent."""
    return ValidationEvent(
        patch_event=patch, result="failed", error_message="test still fails"
    )


def make_engine():
    """Helper to create a mock engine with validator, metrics, etc."""
    engine = MagicMock()
    engine.metrics = MagicMock()
    engine.validator = MagicMock()
    engine.validator.validate = MagicMock()
    return engine


class TestValidateStage:
    """Tests for ValidateStage."""

    # --- name ---

    def test_name(self):
        stage = ValidateStage()
        assert stage.name == "validate"

    # --- no patches ---

    def test_no_patches_empty_list(self):
        """Empty patches list should produce an error ValidationEvent."""
        stage = ValidateStage()
        engine = make_engine()
        context = {
            "event": make_failure(),
            "classification": make_classification(),
            "patches": [],
        }

        result = stage.process(context, engine)

        assert "final_validation" in result
        final = result["final_validation"]
        assert final.result == "error"
        assert "No patches" in final.error_message
        engine.validator.validate.assert_not_called()

    def test_no_patches_key_missing(self):
        """Missing 'patches' key should produce an error ValidationEvent."""
        stage = ValidateStage()
        engine = make_engine()
        context = {
            "event": make_failure(),
            "classification": make_classification(),
        }

        result = stage.process(context, engine)

        final = result["final_validation"]
        assert final.result == "error"
        assert "No patches" in final.error_message

    def test_no_patches_without_classification(self):
        """Empty patches without classification should still work."""
        stage = ValidateStage()
        engine = make_engine()
        context = {
            "event": make_failure(),
            "patches": [],
        }

        result = stage.process(context, engine)

        final = result["final_validation"]
        assert final.result == "error"
        # Should build a default classification internally
        assert final.patch_event.classification_event.category == "unknown"

    # --- single patch ---

    def test_single_patch_passed(self):
        """A single passing patch should produce a passed final."""
        stage = ValidateStage()
        engine = make_engine()
        patch = make_patch()
        engine.validator.validate.return_value = make_passed(patch)

        context = {
            "event": make_failure(),
            "classification": make_classification(),
            "patches": [patch],
        }

        result = stage.process(context, engine)

        final = result["final_validation"]
        assert final.result == "passed"
        engine.validator.validate.assert_called_once_with(patch)

    def test_single_patch_failed(self):
        """A single failing patch should produce a failed final."""
        stage = ValidateStage()
        engine = make_engine()
        patch = make_patch()
        engine.validator.validate.return_value = make_failed(patch)

        context = {
            "event": make_failure(),
            "patches": [patch],
        }

        result = stage.process(context, engine)

        final = result["final_validation"]
        assert final.result == "failed"
        engine.validator.validate.assert_called_once_with(patch)

    # --- multiple patches ---

    def test_multiple_patches_first_fails_second_passes(self):
        """First patch fails, second passes → stop and return passed."""
        stage = ValidateStage()
        engine = make_engine()
        patch1 = make_patch(patch_id="p1")
        patch2 = make_patch(patch_id="p2")

        def validate_side_effect(patch):
            if patch.patch_id == "p1":
                return make_failed(patch)
            return make_passed(patch)

        engine.validator.validate.side_effect = validate_side_effect

        context = {
            "event": make_failure(),
            "patches": [patch1, patch2],
        }

        result = stage.process(context, engine)

        final = result["final_validation"]
        assert final.result == "passed"
        # Should have called validate twice: patch1 and patch2
        assert engine.validator.validate.call_count == 2

    def test_multiple_patches_all_fail(self):
        """All patches fail → return failed with last patch."""
        stage = ValidateStage()
        engine = make_engine()
        patches = [make_patch(patch_id=f"p{i}") for i in range(3)]
        engine.validator.validate.side_effect = lambda p: make_failed(p)

        context = {
            "event": make_failure(),
            "patches": patches,
        }

        result = stage.process(context, engine)

        final = result["final_validation"]
        assert final.result == "failed"
        # best_validation keeps the first failed validation (original behavior)
        assert final.patch_event.patch_id == "p0"
        assert engine.validator.validate.call_count == 3

    def test_multiple_patches_stops_on_first_pass(self):
        """Validate should stop at the first passing patch."""
        stage = ValidateStage()
        engine = make_engine()
        patches = [make_patch(patch_id=f"p{i}") for i in range(5)]

        call_order = []

        def validate_side_effect(patch):
            call_order.append(patch.patch_id)
            if patch.patch_id == "p2":
                return make_passed(patch)
            return make_failed(patch)

        engine.validator.validate.side_effect = validate_side_effect

        context = {
            "event": make_failure(),
            "patches": patches,
        }

        result = stage.process(context, engine)

        final = result["final_validation"]
        assert final.result == "passed"
        # Should only have validated p0, p1, p2 — then stopped
        assert call_order == ["p0", "p1", "p2"]
        assert engine.validator.validate.call_count == 3

    # --- metrics ---

    def test_records_metrics_for_each_validation(self):
        """Engine.metrics.record_validation should be called per patch."""
        stage = ValidateStage()
        engine = make_engine()
        patches = [make_patch(patch_id="p1"), make_patch(patch_id="p2")]
        engine.validator.validate.return_value = make_passed(patches[0])

        context = {
            "event": make_failure(),
            "patches": patches,
        }

        stage.process(context, engine)

        assert engine.metrics.record_validation.call_count == 1  # stopped at first pass

    def test_records_metrics_all_fail(self):
        """Metrics recorded for every failing patch."""
        stage = ValidateStage()
        engine = make_engine()
        patches = [make_patch(patch_id=f"p{i}") for i in range(3)]
        engine.validator.validate.side_effect = lambda p: make_failed(p)

        context = {
            "event": make_failure(),
            "patches": patches,
        }

        stage.process(context, engine)

        assert engine.metrics.record_validation.call_count == 3

    # --- handles error validation results ---

    def test_error_validation_result(self):
        """An 'error' result should be treated like failed."""
        stage = ValidateStage()
        engine = make_engine()
        patch = make_patch()
        engine.validator.validate.return_value = ValidationEvent(
            patch_event=patch,
            result="error",
            error_message="validator crashed",
        )

        context = {
            "event": make_failure(),
            "patches": [patch],
        }

        result = stage.process(context, engine)

        final = result["final_validation"]
        assert final.result == "error"

    # --- context preservation ---

    def test_does_not_modify_other_context_keys(self):
        """context should preserve existing keys."""
        stage = ValidateStage()
        engine = make_engine()
        patch = make_patch()
        engine.validator.validate.return_value = make_passed(patch)

        context = {
            "event": make_failure(),
            "classification": make_classification(),
            "patches": [patch],
            "custom_key": "custom_value",
        }

        result = stage.process(context, engine)

        assert result["event"] is context["event"]
        assert result["classification"] is context["classification"]
        assert result["patches"] is context["patches"]
        assert result["custom_key"] == "custom_value"

    # --- edge cases ---

    def test_patch_without_classification(self):
        """Patches without classification should still validate."""
        stage = ValidateStage()
        engine = make_engine()

        # PatchEvent requires a classification_event, but let's test
        # with a minimal one
        patch = PatchEvent(
            classification_event=ClassificationEvent(
                original_event=make_failure(),
                category="unknown",
                severity=ErrorSeverity.LOW,
                confidence=0.0,
            ),
            patch_id="minimal",
            patch_content="",
            generator="none",
        )
        engine.validator.validate.return_value = make_failed(patch)

        context = {
            "event": make_failure(),
            "patches": [patch],
        }

        result = stage.process(context, engine)

        final = result["final_validation"]
        assert final.result == "failed"

    def test_large_list_of_patches(self):
        """Validate should handle many patches efficiently."""
        stage = ValidateStage()
        engine = make_engine()
        patches = [make_patch(patch_id=f"p{i}") for i in range(100)]
        engine.validator.validate.side_effect = lambda p: make_failed(p)

        context = {
            "event": make_failure(),
            "patches": patches,
        }

        result = stage.process(context, engine)

        assert result["final_validation"].result == "failed"
        assert engine.validator.validate.call_count == 100

    def test_pass_on_first_patch_from_many(self):
        """First patch passes from many → only one validation call."""
        stage = ValidateStage()
        engine = make_engine()
        patches = [make_patch(patch_id=f"p{i}") for i in range(50)]
        engine.validator.validate.return_value = make_passed(patches[0])

        context = {
            "event": make_failure(),
            "patches": patches,
        }

        result = stage.process(context, engine)

        assert result["final_validation"].result == "passed"
        assert engine.validator.validate.call_count == 1
