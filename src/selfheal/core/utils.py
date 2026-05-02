"""Shared utility functions for selfheal core modules."""

from __future__ import annotations

import hashlib

from selfheal.events import TestFailureEvent


def make_error_signature(event: TestFailureEvent) -> str:
    """Generate a stable hash key from an error event.

    Uses error_type + first meaningful traceback line for uniqueness.
    Human-readable prefix helps debugging.
    """
    tb_first_line = ""
    for line in event.traceback.splitlines():
        line = line.strip()
        if line.startswith("File ") or "Error" in line:
            tb_first_line = line
            break

    raw = f"{event.error_type}|{event.error_message[:200]}|{tb_first_line[:200]}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{event.error_type}:{digest}"
