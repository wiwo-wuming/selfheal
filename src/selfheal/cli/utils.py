"""Shared CLI utilities."""
from typing import Any
from selfheal.events import TestFailureEvent, ClassificationEvent, ErrorSeverity


def reconstruct_failure_event(data: dict[str, Any]) -> TestFailureEvent:
    """Reconstruct a TestFailureEvent from a serialised dict."""
    return TestFailureEvent(
        test_path=data["test_path"],
        error_type=data["error_type"],
        error_message=data["error_message"],
        traceback=data.get("traceback", ""),
    )


def reconstruct_classification_event(data: dict[str, Any]) -> ClassificationEvent:
    """Reconstruct a ClassificationEvent from a serialised dict."""
    original = reconstruct_failure_event(data["original_event"])
    return ClassificationEvent(
        original_event=original,
        category=data["category"],
        severity=ErrorSeverity(data["severity"]),
        confidence=data["confidence"],
        reasoning=data.get("reasoning", ""),
    )


def make_rollback_patch(patch_id: str, info: dict):
    """Create a minimal PatchEvent for rollback operations."""
    from selfheal.events import PatchEvent
    dummy_event = TestFailureEvent(
        test_path=info["target_file"],
        error_type="rolled_back",
        error_message="Manual rollback",
    )
    dummy_classification = ClassificationEvent(
        original_event=dummy_event,
        category="unknown",
        severity=ErrorSeverity.MEDIUM,
        confidence=0.0,
    )
    return PatchEvent(
        classification_event=dummy_classification,
        patch_id=patch_id,
        patch_content="",
        generator="rollback",
        target_file=info["target_file"],
        backup_path=info["backup_path"],
    )
