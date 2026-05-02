"""Tests for LLMResponseCache — error-signature-based caching."""

import time
import pytest

from selfheal.core.cache import (
    LLMResponseCache,
    get_cache,
    reset_cache,
)
from selfheal.core.utils import make_error_signature
from selfheal.events import TestFailureEvent


def make_event(error_type="AssertionError", error_message="assert 1 == 2",
               traceback='File "foo.py", line 42, in test_bar\n    assert 1 == 2'):
    return TestFailureEvent(
        test_path="tests/test_foo.py::test_bar",
        error_type=error_type,
        error_message=error_message,
        traceback=traceback,
    )


class TestErrorSignature:
    """Tests for cache key generation."""

    def test_same_error_same_signature(self):
        """Identical errors produce identical cache keys."""
        e1 = make_event("AssertionError", "assert 1 == 2")
        e2 = make_event("AssertionError", "assert 1 == 2")
        assert make_error_signature(e1) == make_error_signature(e2)

    def test_different_error_type_different_signature(self):
        """Different error types produce different keys."""
        e1 = make_event("AssertionError")
        e2 = make_event("ImportError")
        assert make_error_signature(e1) != make_error_signature(e2)

    def test_signature_starts_with_error_type(self):
        """Signature is human-readable, starts with error type."""
        sig = make_error_signature(make_event("ValueError", "bad value"))
        assert sig.startswith("ValueError:")

    def test_different_message_different_signature(self):
        """Different error messages produce different keys."""
        e1 = make_event("RuntimeError", "msg A")
        e2 = make_event("RuntimeError", "msg B")
        assert make_error_signature(e1) != make_error_signature(e2)

    def test_different_traceback_different_signature(self):
        """Different tracebacks produce different keys."""
        e1 = make_event("RuntimeError", "msg", 'File "a.py", line 1\nError')
        e2 = make_event("RuntimeError", "msg", 'File "b.py", line 99\nError')
        assert make_error_signature(e1) != make_error_signature(e2)


class TestLLMResponseCache:
    """Unit tests for LLMResponseCache."""

    def test_cache_miss_returns_none(self):
        cache = LLMResponseCache()
        assert cache.get("nonexistent") is None

    def test_cache_hit_returns_data(self):
        cache = LLMResponseCache()
        key = "test:abc123"
        data = {"category": "runtime", "severity": "medium", "confidence": 0.8}
        cache.set(key, data)
        assert cache.get(key) == data

    def test_cache_expiry(self):
        cache = LLMResponseCache(ttl=0.01)  # 10ms TTL
        key = "test:expired"
        cache.set(key, {"x": 1})
        time.sleep(0.02)
        assert cache.get(key) is None

    def test_cache_max_size_eviction(self):
        cache = LLMResponseCache(max_size=3)
        for i in range(5):
            cache.set(f"key:{i}", {"i": i})
        assert len(cache) == 3  # oldest 2 evicted

    def test_cache_invalidate_specific_key(self):
        cache = LLMResponseCache()
        cache.set("a", {"x": 1})
        cache.set("b", {"x": 2})
        cache.invalidate("a")
        assert cache.get("a") is None
        assert cache.get("b") == {"x": 2}

    def test_cache_invalidate_all(self):
        cache = LLMResponseCache()
        cache.set("a", {"x": 1})
        cache.set("b", {"x": 2})
        cache.invalidate()
        assert len(cache) == 0

    def test_cache_stats(self):
        cache = LLMResponseCache()
        cache.get("miss1")
        cache.get("miss2")
        cache.set("hit", {"x": 1})
        cache.get("hit")
        cache.get("hit")

        stats = cache.stats
        assert stats["hits"] == 2
        assert stats["misses"] == 2
        assert stats["size"] == 1

    def test_make_key_method(self):
        cache = LLMResponseCache()
        event = make_event("ValueError", "bad")
        key = cache.make_key(event)
        assert key.startswith("ValueError:")
        assert len(key) > 10

    def test_end_to_end_flow(self):
        """Full cache flow: miss → set → hit."""
        cache = LLMResponseCache()
        event = make_event("KeyError", "missing key 'x'")
        key = cache.make_key(event)

        # First call: miss
        assert cache.get(key) is None
        # Store result
        cache.set(key, {"category": "runtime", "severity": "medium", "confidence": 0.7})
        # Second call: hit
        cached = cache.get(key)
        assert cached is not None
        assert cached["category"] == "runtime"


class TestGlobalCacheSingleton:
    """Tests for module-level cache singleton."""

    def setup_method(self):
        reset_cache()

    def teardown_method(self):
        reset_cache()

    def test_get_cache_returns_same_instance(self):
        c1 = get_cache()
        c2 = get_cache()
        assert c1 is c2

    def test_reset_cache_creates_new_instance(self):
        c1 = get_cache()
        reset_cache()
        c2 = get_cache()
        assert c1 is not c2
