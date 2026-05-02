"""LLM response cache with error-signature-based keys and TTL."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from selfheal.core.utils import make_error_signature
from selfheal.events import TestFailureEvent

logger = logging.getLogger(__name__)

# Default TTL: 1 hour — same error within an hour reuses cached result
DEFAULT_CACHE_TTL = 3600


class LLMResponseCache:
    """In-memory cache for LLM responses keyed by error signature.

    Usage::

        cache = LLMResponseCache(ttl=3600)
        key = cache.make_key(event)
        cached = cache.get(key)
        if cached is None:
            result = call_llm(...)
            cache.set(key, result)
    """

    def __init__(self, ttl: float = DEFAULT_CACHE_TTL, max_size: int = 1000):
        self._ttl = ttl
        self._max_size = max_size
        self._store: dict[str, dict[str, Any]] = {}
        self._hits: int = 0
        self._misses: int = 0

    @staticmethod
    def make_key(event: TestFailureEvent) -> str:
        """Generate a cache key for a failure event."""
        return make_error_signature(event)

    def get(self, key: str) -> Optional[dict[str, Any]]:
        """Retrieve a cached response if not expired."""
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None

        if time.time() - entry["cached_at"] > self._ttl:
            del self._store[key]
            self._misses += 1
            return None

        self._hits += 1
        return entry["data"]

    def set(self, key: str, data: dict[str, Any]) -> None:
        """Store a response in the cache."""
        # Evict oldest entry if at capacity
        if len(self._store) >= self._max_size:
            oldest_key = min(self._store, key=lambda k: self._store[k]["cached_at"])
            del self._store[oldest_key]

        self._store[key] = {
            "data": data,
            "cached_at": time.time(),
        }

    def invalidate(self, key: Optional[str] = None) -> None:
        """Invalidate a specific key or the entire cache."""
        if key is not None:
            self._store.pop(key, None)
        else:
            self._store.clear()

    @property
    def stats(self) -> dict[str, Any]:
        """Return cache hit/miss statistics."""
        total = self._hits + self._misses
        return {
            "size": len(self._store),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": (self._hits / total * 100) if total > 0 else 0.0,
            "max_size": self._max_size,
            "ttl_seconds": self._ttl,
        }

    def __len__(self) -> int:
        return len(self._store)


# Module-level singleton for easy cross-component sharing
_cache_instance: Optional[LLMResponseCache] = None


def get_cache(ttl: float = DEFAULT_CACHE_TTL) -> LLMResponseCache:
    """Get or create the module-level cache singleton."""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = LLMResponseCache(ttl=ttl)
    return _cache_instance


def reset_cache() -> None:
    """Reset the global cache singleton (useful in tests)."""
    global _cache_instance
    _cache_instance = None
