"""Tests for ExperienceStore — fix experience learning."""

import os
import tempfile
from pathlib import Path

import pytest

from selfheal.core.experience import (
    ExperienceStore,
    get_experience,
    reset_experience,
    _make_error_signature,
)
from selfheal.events import (
    TestFailureEvent,
    ClassificationEvent,
    PatchEvent,
    ErrorSeverity,
)


def make_event(error_type="AssertionError", error_message="assert 1 == 2",
               traceback='File "foo.py", line 42, in test_bar\n    assert 1 == 2'):
    return TestFailureEvent(
        test_path="tests/test_foo.py::test_bar",
        error_type=error_type,
        error_message=error_message,
        traceback=traceback,
    )


def make_classification(event=None, category="assertion", severity=None):
    return ClassificationEvent(
        original_event=event or make_event(),
        category=category,
        severity=severity or ErrorSeverity.MEDIUM,
        confidence=0.8,
    )


def make_patch(classification=None, content="# fix\ndef fix_foo():\n    return 42"):
    return PatchEvent(
        classification_event=classification or make_classification(),
        patch_id="test-patch-001",
        patch_content=content,
        generator="template",
    )


@pytest.fixture
def temp_db():
    """Provide a temporary experience database."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_path = Path(path)
    yield db_path
    db_path.unlink(missing_ok=True)
    # Clean up -wal and -shm files
    for suffix in (".db-wal", ".db-shm"):
        p = db_path.with_suffix(suffix)
        p.unlink(missing_ok=True)


@pytest.fixture
def store(temp_db):
    """Provide an ExperienceStore backed by a temp database."""
    s = ExperienceStore(db_path=str(temp_db))
    yield s
    s.close()


class TestExperienceStore:
    """Unit tests for ExperienceStore."""

    def test_record_and_find_exact_match(self, store):
        """Record a successful patch and find it by exact signature."""
        event = make_event("AssertionError", "assert 1 == 2")
        classification = make_classification(event, "assertion")
        patch = make_patch(classification)

        store.record_success(event, classification, patch)

        results = store.find_similar(event, category="assertion")
        assert len(results) >= 1
        assert results[0]["patch_content"] == patch.patch_content
        assert results[0]["category"] == "assertion"

    def test_duplicate_record_increments_count(self, store):
        """Recording the same patch twice increments success_count."""
        event = make_event()
        classification = make_classification(event)
        patch = make_patch(classification)

        store.record_success(event, classification, patch)
        store.record_success(event, classification, patch)

        results = store.find_similar(event)
        assert results[0]["success_count"] == 2

    def test_find_by_same_error_type(self, store):
        """Falls back to same error_type when no exact signature match."""
        event1 = make_event("ValueError", "bad value A")
        event2 = make_event("ValueError", "bad value B")  # different msg → different sig

        store.record_success(event1, make_classification(event1, "runtime"),
                             make_patch(content="# fix A"))
        store.record_success(event2, make_classification(event2, "runtime"),
                             make_patch(content="# fix B"))

        # event3 has same error_type (ValueError) but different message
        event3 = make_event("ValueError", "bad value C")
        results = store.find_similar(event3, category="runtime")

        assert len(results) >= 2  # both ValueErrors should match

    def test_find_by_category_fallback(self, store):
        """Falls back to same category when error_type doesn't match."""
        store.record_success(
            make_event("KeyError", "missing key"),
            make_classification(category="runtime"),
            make_patch(content="# runtime fix"),
        )

        # Search with different error_type but same category
        event = make_event("IndexError", "list index out of range")
        results = store.find_similar(event, category="runtime")

        assert len(results) >= 1
        assert results[0]["category"] == "runtime"

    def test_find_similar_limit(self, store):
        """Results respect the limit parameter."""
        for i in range(10):
            store.record_success(
                make_event("RuntimeError", f"error {i}"),
                make_classification(category="runtime"),
                make_patch(content=f"# fix {i}"),
            )

        results = store.find_similar(
            make_event("RuntimeError", "error X"),
            category="runtime",
            limit=3,
        )
        assert len(results) <= 3

    def test_find_similar_empty(self, store):
        """No results for an empty store."""
        results = store.find_similar(make_event())
        assert results == []

    def test_stats(self, store):
        """Stats returns meaningful data."""
        for i in range(3):
            store.record_success(
                make_event("RuntimeError", f"msg {i}"),
                make_classification(category="runtime"),
                make_patch(content=f"# fix {i}"),
            )

        store.record_success(
            make_event("ImportError", "no module X"),
            make_classification(category="import"),
            make_patch(content="# import fix"),
        )

        stats = store.stats()
        assert stats["total_experiences"] == 4
        assert stats["unique_signatures"] >= 2
        assert len(stats["top_categories"]) >= 1
        assert stats["total_successes"] == 4

    def test_prune_removes_stale(self, store):
        """Pruning removes entries with low success_count."""
        store.record_success(
            make_event("RuntimeError", "rare error"),
            make_classification(category="runtime"),
            make_patch(content="# rare fix"),
        )

        # Prune with high min_success_count
        removed = store.prune(min_success_count=2)
        assert removed >= 1

        results = store.find_similar(make_event("RuntimeError", "rare error"))
        assert results == []

    def test_results_sorted_by_success_count(self, store):
        """Results are ordered by success_count DESC."""
        event = make_event("RuntimeError", "msg")
        classification = make_classification(event, "runtime")

        patch1 = make_patch(content="# best fix")
        patch2 = make_patch(content="# ok fix")

        store.record_success(event, classification, patch2)  # count=1
        for _ in range(5):
            store.record_success(event, classification, patch1)  # count=5

        results = store.find_similar(event, limit=2)
        assert results[0]["patch_content"] == "# best fix"
        assert results[0]["success_count"] == 5
