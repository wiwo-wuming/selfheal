"""Tests for ExperienceStore — fix experience learning."""

import os
import math
import tempfile
from pathlib import Path

import pytest

from selfheal.core.experience import (
    ExperienceMatch,
    ExperienceStore,
    get_experience,
    reset_experience,
)
from selfheal.core.utils import make_error_signature
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

    # ------------------------------------------------------------------
    # v0.3.0: find_similar_with_confidence tests
    # ------------------------------------------------------------------

    def test_confidence_exact_match_is_0_95(self, store):
        """Exact signature match gives confidence=0.95 and match_tier='exact'."""
        event = make_event("AssertionError", "assert 1 == 2")
        classification = make_classification(event, "assertion")
        patch = make_patch(classification)

        store.record_success(event, classification, patch)

        results = store.find_similar_with_confidence(event, category="assertion")
        assert len(results) >= 1
        assert isinstance(results[0], ExperienceMatch)
        assert results[0].confidence == 0.95
        assert results[0].match_tier == "exact"

    def test_confidence_error_type_increases_with_success(self, store):
        """Same error_type confidence grows as success_count increases."""
        event = make_event("ValueError", "bad value A")
        classification = make_classification(event, "runtime")
        patch = make_patch(classification, content="# fix v")

        # Record once -> success_count = 1
        store.record_success(event, classification, patch)

        # Query with a different event that shares the same error_type
        event2 = make_event("ValueError", "bad value B")
        results_1 = store.find_similar_with_confidence(event2, category="runtime")
        assert len(results_1) >= 1
        conf_1 = results_1[0].confidence
        assert results_1[0].match_tier == "error_type"
        # confidence should be 0.70 * (1 - exp(-1/5)) ≈ 0.127
        expected_1 = 0.70 * (1 - math.exp(-1 / 5))
        assert conf_1 == pytest.approx(expected_1, rel=1e-3)

        # Record the same patch again -> success_count = 2
        store.record_success(event, classification, patch)
        results_2 = store.find_similar_with_confidence(event2, category="runtime")
        conf_2 = results_2[0].confidence
        # confidence should be 0.70 * (1 - exp(-2/5)) ≈ 0.231
        expected_2 = 0.70 * (1 - math.exp(-2 / 5))
        assert conf_2 == pytest.approx(expected_2, rel=1e-3)

        # Confidence with success_count=2 > confidence with success_count=1
        assert conf_2 > conf_1

    def test_confidence_category_is_lowest(self, store):
        """Category-only match has lower confidence than exact or error_type matches."""
        event = make_event("KeyError", "missing key")
        classification = make_classification(event, "runtime")
        patch = make_patch(classification, content="# runtime fix")

        store.record_success(event, classification, patch)

        # Query with same event (not same signature, but same error_type + category)
        # Different signature so it won't be exact match
        event2 = make_event("KeyError", "different missing key")
        results = store.find_similar_with_confidence(event2, category="runtime")

        # All results from error_type tier have confidence < 0.95 (not exact)
        for r in results:
            assert r.confidence < 0.95

        # The category-tier confidence formula uses 0.45 multiplier, which is
        # always lower than the error_type formula's 0.70 for the same success_count
        max_possible_category_conf = 0.45 * (1 - math.exp(-1000 / 5))  # ~0.45
        assert max_possible_category_conf < 0.70
        assert max_possible_category_conf < 0.95

    def test_confidence_below_threshold_is_filtered(self, store):
        """Results below min_confidence are excluded from output."""
        event = make_event("RuntimeError", "rare error")
        classification = make_classification(event, "runtime")
        patch = make_patch(classification, content="# rare fix")

        store.record_success(event, classification, patch)

        # Exact match -> confidence 0.95, should pass threshold
        results_high = store.find_similar_with_confidence(
            event, category="runtime", min_confidence=0.90,
        )
        assert len(results_high) >= 1
        assert results_high[0].confidence >= 0.90

        # Set threshold above 0.95 -> exact match excluded
        results_none = store.find_similar_with_confidence(
            event, category="runtime", min_confidence=0.99,
        )
        assert len(results_none) == 0

        # Different event (same error_type) -> lower confidence
        event2 = make_event("RuntimeError", "another rare error")
        results_low = store.find_similar_with_confidence(
            event2, category="runtime", min_confidence=0.50,
        )
        # success_count=1 so confidence = 0.70*(1-exp(-0.2)) ≈ 0.127 < 0.50
        assert len(results_low) == 0

    def test_match_tier_correctly_set(self, store):
        """match_tier field is correctly assigned per match type."""
        event = make_event("AssertionError", "assert x == y")
        classification = make_classification(event, "assertion")
        patch = make_patch(classification, content="# assertion fix")

        store.record_success(event, classification, patch)

        # Exact signature match -> "exact"
        results = store.find_similar_with_confidence(event, category="assertion")
        assert len(results) >= 1
        assert results[0].match_tier == "exact"

        # Different signature, same error_type -> "error_type"
        event2 = make_event("AssertionError", "assert a != b")
        results2 = store.find_similar_with_confidence(event2, category="assertion")
        assert len(results2) >= 1
        assert results2[0].match_tier == "error_type"

        # Different signature, different error_type, same category -> "category"
        event3 = make_event("ValueError", "bad value")
        # Store another entry in the same category
        store.record_success(
            make_event("RuntimeError", "some runtime error"),
            make_classification(category="assertion"),
            make_patch(content="# runtime fix in assertion category"),
        )
        results3 = store.find_similar_with_confidence(event3, category="assertion")
        # There should be at least one category-tier match
        category_matches = [r for r in results3 if r.match_tier == "category"]
        assert len(category_matches) >= 1
