"""Raw log file watcher implementation."""

import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from selfheal.config import WatcherConfig
from selfheal.events import TestFailureEvent
from selfheal.interfaces.watcher import WatcherInterface

logger = logging.getLogger(__name__)


class RawLogWatcher(WatcherInterface):
    """Watches raw log files for error patterns."""

    def __init__(self, config: WatcherConfig):
        self.config = config
        self._running = False
        self._file_positions: dict[Path, int] = {}
        self._thread: Optional[threading.Thread] = None

    name = "raw_log"

    def start(self, paths: list[str], callback: Callable[[Any], None]) -> None:
        """Start watching log files."""
        self._running = True
        self._thread = threading.Thread(
            target=self._watch_files,
            args=(paths, callback),
            daemon=True,
        )
        self._thread.start()
        logger.info(f"RawLogWatcher started for paths: {paths}")

    def stop(self) -> None:
        """Stop watching."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("RawLogWatcher stopped")

    def _watch_files(self, paths: list[str], callback: Callable[[TestFailureEvent], None]) -> None:
        """Watch multiple log files for changes."""
        log_paths = [Path(p) for p in paths]
        while self._running:
            for path in log_paths:
                if not path.exists():
                    continue
                current_size = path.stat().st_size
                last_pos = self._file_positions.get(path, current_size)
                # Detect file truncation (log rotation) — reset to beginning
                if current_size < last_pos:
                    last_pos = 0
                    self._file_positions[path] = 0
                if current_size > last_pos:
                    try:
                        with open(path, "r", encoding="utf-8", errors="ignore") as f:
                            f.seek(last_pos)
                            new_content = f.read()
                            self._file_positions[path] = f.tell()
                            failures = self._parse_errors(new_content)
                            for failure in failures:
                                callback(failure)
                    except Exception as e:
                        logger.error(f"Error reading log file {path}: {e}")
            time.sleep(1)

    def _parse_errors(self, content: str) -> list[TestFailureEvent]:
        """Parse error patterns from log content."""
        failures = []

        # Common error patterns
        error_patterns = [
            re.compile(r"(?P<error_type>\w+Error):\s+(?P<msg>.+)", re.MULTILINE),
            re.compile(r"FAILED\s+(?P<test>.+?)\s+- (?P<msg>.+)", re.MULTILINE),
            re.compile(r"ERROR\s+(?P<test>.+?)\s+- (?P<msg>.+)", re.MULTILINE),
        ]

        for pattern in error_patterns:
            groupdict = pattern.groupindex
            for match in pattern.finditer(content):
                if "error_type" in groupdict:
                    # Pattern: \w+Error: ...
                    test_path = match.group("test") if "test" in groupdict else "unknown"
                    failure = TestFailureEvent(
                        test_path=test_path,
                        error_type=match.group("error_type"),
                        error_message=match.group("msg").strip(),
                        traceback=content[max(0, match.start() - 200):match.end() + 200],
                    )
                else:
                    # Pattern: FAILED/ERROR ... - ...
                    test_path = match.group("test") if "test" in groupdict else "unknown"
                    failure = TestFailureEvent(
                        test_path=test_path,
                        error_type="LogError",
                        error_message=match.group("msg").strip(),
                        traceback=content[max(0, match.start() - 200):match.end() + 200],
                    )
                failures.append(failure)

        return failures
