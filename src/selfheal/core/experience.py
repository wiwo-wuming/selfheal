"""Fix experience store: learn from successful patches and reuse them.

Stores validated patches indexed by error signature in SQLite.
When a similar failure occurs, retrieves candidate patches ranked by
success count and recency.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from selfheal.events import TestFailureEvent, ClassificationEvent, PatchEvent, ValidationEvent

logger = logging.getLogger(__name__)

# Same key derivation as cache.py for consistency
_EXPERIENCE_DB_NAME = ".selfheal/experience.db"


def _make_error_signature(event: TestFailureEvent) -> str:
    """Generate a stable hash key from an error event."""
    tb_first_line = ""
    for line in event.traceback.splitlines():
        line = line.strip()
        if line.startswith("File ") or "Error" in line:
            tb_first_line = line
            break
    raw = f"{event.error_type}|{event.error_message[:200]}|{tb_first_line[:200]}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{event.error_type}:{digest}"


class ExperienceStore:
    """Persistent store of successful fix experiences.

    Schema::

        CREATE TABLE experiences (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            signature   TEXT NOT NULL,
            category    TEXT NOT NULL,
            error_type  TEXT NOT NULL,
            error_msg   TEXT,
            patch_content TEXT NOT NULL,
            generator   TEXT NOT NULL,
            success_count INTEGER DEFAULT 1,
            last_used   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX idx_signature ON experiences(signature);
        CREATE INDEX idx_category ON experiences(category);
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path or _EXPERIENCE_DB_NAME)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self) -> None:
        """Create tables and indexes if they don't exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS experiences (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                signature   TEXT NOT NULL,
                category    TEXT NOT NULL,
                error_type  TEXT NOT NULL,
                error_msg   TEXT DEFAULT '',
                patch_content TEXT NOT NULL,
                generator   TEXT NOT NULL DEFAULT 'template',
                success_count INTEGER DEFAULT 1,
                last_used   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_signature ON experiences(signature)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_category ON experiences(category)")
        conn.commit()
        logger.info("ExperienceStore initialized at %s", self.db_path)

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def record_success(
        self,
        event: TestFailureEvent,
        classification: ClassificationEvent,
        patch: PatchEvent,
    ) -> None:
        """Store a patch that was validated successfully.

        If an identical (signature + patch_content) entry already exists,
        increment its success_count instead of inserting a duplicate.
        """
        signature = _make_error_signature(event)
        conn = self._get_conn()

        # Check for identical existing entry
        existing = conn.execute(
            "SELECT id, success_count FROM experiences "
            "WHERE signature = ? AND patch_content = ?",
            (signature, patch.patch_content),
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE experiences SET success_count = ?, last_used = ? WHERE id = ?",
                (existing["success_count"] + 1, datetime.now().isoformat(), existing["id"]),
            )
            conn.commit()
            logger.debug(
                "Experience: incremented success_count for signature=%s (now %d)",
                signature, existing["success_count"] + 1,
            )
            return

        conn.execute(
            """INSERT INTO experiences
               (signature, category, error_type, error_msg, patch_content, generator)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                signature,
                classification.category,
                event.error_type,
                event.error_message[:500],
                patch.patch_content,
                patch.generator,
            ),
        )
        conn.commit()
        logger.info("Experience: stored new fix for signature=%s category=%s", signature, classification.category)

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def find_similar(
        self,
        event: TestFailureEvent,
        category: Optional[str] = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Find past successful patches for a similar error.

        Search strategy:
        1. Exact signature match (highest priority)
        2. Same error_type (fallback)
        3. Same category (broadest)

        Results are ordered by success_count DESC, last_used DESC.
        """
        signature = _make_error_signature(event)
        conn = self._get_conn()
        results: list[dict[str, Any]] = []

        # Strategy 1: Exact signature match
        rows = conn.execute(
            """SELECT * FROM experiences WHERE signature = ?
               ORDER BY success_count DESC, last_used DESC LIMIT ?""",
            (signature, limit),
        ).fetchall()
        results.extend(self._rows_to_dicts(rows))

        # Strategy 2: Same error_type (if we don't have enough)
        if len(results) < limit:
            existing_sigs = {r["signature"] for r in results}
            rows = conn.execute(
                """SELECT * FROM experiences
                   WHERE error_type = ? AND signature NOT IN ({})
                   ORDER BY success_count DESC, last_used DESC LIMIT ?""".format(
                    ",".join("?" * len(existing_sigs)) if existing_sigs else "''"
                ),
                [event.error_type, *existing_sigs, limit - len(results)],
            ).fetchall()
            results.extend(self._rows_to_dicts(rows))

        # Strategy 3: Same category
        if category and len(results) < limit:
            existing_ids = {r["id"] for r in results}
            ids_placeholder = ",".join("?" * len(existing_ids)) if existing_ids else "''"
            rows = conn.execute(
                f"""SELECT * FROM experiences
                   WHERE category = ? AND id NOT IN ({ids_placeholder})
                   ORDER BY success_count DESC, last_used DESC LIMIT ?""",
                [category, *existing_ids, limit - len(results)],
            ).fetchall()
            results.extend(self._rows_to_dicts(rows))

        return results[:limit]

    def _rows_to_dicts(self, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Return summary statistics."""
        conn = self._get_conn()
        return {
            "total_experiences": conn.execute("SELECT COUNT(*) FROM experiences").fetchone()[0],
            "unique_signatures": conn.execute(
                "SELECT COUNT(DISTINCT signature) FROM experiences"
            ).fetchone()[0],
            "top_categories": [
                dict(r) for r in conn.execute(
                    "SELECT category, COUNT(*) as cnt FROM experiences "
                    "GROUP BY category ORDER BY cnt DESC LIMIT 5"
                ).fetchall()
            ],
            "total_successes": conn.execute(
                "SELECT COALESCE(SUM(success_count), 0) FROM experiences"
            ).fetchone()[0],
        }

    def prune(self, max_age_days: int = 90, min_success_count: int = 1) -> int:
        """Remove old or rarely-successful entries. Returns count removed."""
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM experiences WHERE "
            "last_used < ? OR success_count < ?",
            (datetime.now().isoformat()[:10], min_success_count),
        )
        conn.commit()
        removed = cursor.rowcount
        if removed:
            logger.info("Experience: pruned %d stale entries", removed)
        return removed

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


# Module-level singleton
_experience_instance: Optional[ExperienceStore] = None


def get_experience(db_path: Optional[str] = None) -> ExperienceStore:
    """Get or create the module-level ExperienceStore singleton."""
    global _experience_instance
    if _experience_instance is None:
        _experience_instance = ExperienceStore(db_path=db_path)
    return _experience_instance


def reset_experience() -> None:
    """Reset the global singleton (useful in tests)."""
    global _experience_instance
    _experience_instance = None
