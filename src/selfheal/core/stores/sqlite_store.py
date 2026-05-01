"""SQLite store implementation."""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from selfheal.config import StoreConfig
from selfheal.events import (
    TestFailureEvent,
    ClassificationEvent,
    PatchEvent,
    ValidationEvent,
    ErrorSeverity,
)
from selfheal.interfaces.store import StoreInterface

logger = logging.getLogger(__name__)


class SQLiteStore(StoreInterface):
    """SQLite-based persistent event store."""

    def __init__(self, config: StoreConfig):
        self.config = config
        self.db_path = Path(config.db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the database schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                event_data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_event_type
            ON events(event_type)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_created_at
            ON events(created_at)
        """)

        conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    name = "sqlite"

    def save_events(self, events: list[Any]) -> None:
        """Save events to SQLite."""
        conn = self._get_conn()
        cursor = conn.cursor()

        for event in events:
            event_type = self._get_event_type(event)
            event_data = json.dumps(event.to_dict(), default=str)

            cursor.execute(
                "INSERT INTO events (event_type, event_data) VALUES (?, ?)",
                (event_type, event_data),
            )

        conn.commit()
        logger.info(f"Saved {len(events)} events to SQLite")

    def get_events(self, event_type: str, limit: int = 100) -> list[Any]:
        """Get events from SQLite."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT event_data FROM events WHERE event_type = ? ORDER BY created_at DESC LIMIT ?",
            (event_type, limit),
        )

        rows = cursor.fetchall()
        events = []

        for row in rows:
            event_data = json.loads(row["event_data"])
            event = self._deserialize_event(event_type, event_data)
            if event:
                events.append(event)

        return events

    def _get_event_type(self, event: Any) -> str:
        """Get the event type name."""
        type_map = {
            TestFailureEvent: "failure",
            ClassificationEvent: "classification",
            PatchEvent: "patch",
            ValidationEvent: "validation",
        }
        return type_map.get(type(event), "unknown")

    def _deserialize_event(self, event_type: str, data: dict) -> Optional[Any]:
        """Deserialize event from dict."""
        if event_type == "failure":
            return TestFailureEvent(
                test_path=data["test_path"],
                error_type=data["error_type"],
                error_message=data["error_message"],
                traceback=data.get("traceback", ""),
                timestamp=datetime.fromisoformat(data["timestamp"]),
                metadata=data.get("metadata", {}),
            )
        elif event_type == "classification":
            original_data = data.get("original_event", {})
            original = TestFailureEvent(
                test_path=original_data["test_path"],
                error_type=original_data["error_type"],
                error_message=original_data["error_message"],
                traceback=original_data.get("traceback", ""),
                timestamp=datetime.fromisoformat(original_data["timestamp"]) if original_data.get("timestamp") else datetime.now(),
                metadata=original_data.get("metadata", {}),
            )
            return ClassificationEvent(
                original_event=original,
                category=data["category"],
                severity=ErrorSeverity(data["severity"]),
                confidence=float(data["confidence"]),
                reasoning=data.get("reasoning", ""),
                alternative_categories=data.get("alternative_categories", []),
            )
        elif event_type == "patch":
            classification_data = data.get("classification_event", {})
            classification = self._deserialize_event("classification", classification_data)
            if classification is None:
                logger.warning(f"Failed to deserialize classification for patch {data.get('patch_id')}")
                return None
            return PatchEvent(
                classification_event=classification,
                patch_id=data["patch_id"],
                patch_content=data["patch_content"],
                generator=data["generator"],
                status=data.get("status", "generated"),
                applied_at=datetime.fromisoformat(data["applied_at"]) if data.get("applied_at") else None,
            )
        elif event_type == "validation":
            patch_data = data.get("patch_event", {})
            patch = self._deserialize_event("patch", patch_data)
            if patch is None:
                logger.warning(f"Failed to deserialize patch for validation event")
                return None
            return ValidationEvent(
                patch_event=patch,
                result=data["result"],
                test_output=data.get("test_output", ""),
                duration=float(data.get("duration", 0.0)),
                error_message=data.get("error_message", ""),
                timestamp=datetime.fromisoformat(data["timestamp"]) if data.get("timestamp") else datetime.now(),
            )
        return None

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
