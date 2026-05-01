"""Pytest watcher implementation."""

import logging
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional, Set

from selfheal.config import WatcherConfig
from selfheal.events import TestFailureEvent
from selfheal.interfaces.watcher import WatcherInterface

logger = logging.getLogger(__name__)


class PytestWatcher(WatcherInterface):
    """Watches pytest execution and captures failures.

    Continuously runs pytest and detects new failures.
    Also watches for file system changes to trigger re-runs.
    """

    def __init__(self, config: WatcherConfig):
        self.config = config
        self._running = False
        self._process: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._known_failures: Set[str] = set()  # track seen failures
        self._file_state: dict[Path, float] = {}  # file -> mtime

    name = "pytest"

    def start(self, paths: list[str], callback: Callable[[Any], None]) -> None:
        """Start watching pytest execution."""
        self._running = True
        self._thread = threading.Thread(
            target=self._watch_loop,
            args=(paths, callback),
            daemon=True,
        )
        self._thread.start()
        logger.info(f"PytestWatcher started for paths: {paths}")

    def stop(self) -> None:
        """Stop watching."""
        self._running = False
        if self._process:
            self._process.terminate()
            self._process.wait()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("PytestWatcher stopped")

    def _watch_loop(self, paths: list[str], callback: Callable[[Any], None]) -> None:
        """Main watch loop — continuously runs pytest and detects changes."""
        cmd = [
            "pytest",
            *self.config.pytest_args,
            *paths,
        ]

        interval = self.config.poll_interval
        watch_paths = [Path(p) for p in paths]
        # Include source directories for file watching
        watch_dirs = set()
        for p in watch_paths:
            if p.is_dir():
                watch_dirs.add(p)
            elif p.is_file():
                watch_dirs.add(p.parent)
            elif p.parent.exists():
                watch_dirs.add(p.parent)

        if not watch_dirs:
            watch_dirs.add(Path.cwd())

        # Initial snapshot of file states
        self._update_file_snapshot(watch_dirs)

        while self._running:
            logger.info(f"Running pytest: {' '.join(cmd)}")

            # Run pytest
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.config.poll_interval * 10,
                )
            except subprocess.TimeoutExpired:
                logger.warning("pytest run timed out")
                time.sleep(interval)
                continue

            # Parse and dispatch new failures only
            output = result.stdout + result.stderr
            failures = self._parse_failures(output)
            for failure in failures:
                failure_key = f"{failure.test_path}:{failure.error_type}"
                if failure_key not in self._known_failures:
                    self._known_failures.add(failure_key)
                    callback(failure)

            # Wait for file changes or poll interval
            # Use precise fractional sleep: check every second, or the remaining time
            if interval <= 0:
                interval = 5.0
            elapsed = 0.0
            check_step = min(1.0, interval)
            while elapsed < interval:
                if not self._running:
                    break
                if self._detect_file_changes(watch_dirs):
                    logger.info("File change detected, re-running pytest")
                    break
                time.sleep(check_step)
                elapsed += check_step

    def _update_file_snapshot(self, watch_dirs: Set[Path]) -> None:
        """Record current mtime for all files in watch directories."""
        patterns = self.config.watch_patterns
        for wd in watch_dirs:
            if not wd.exists():
                continue
            for pattern in patterns:
                for f in wd.rglob(pattern):
                    if f.is_file():
                        self._file_state[f] = f.stat().st_mtime

    def _detect_file_changes(self, watch_dirs: Set[Path]) -> bool:
        """Check if any watched file has been modified."""
        patterns = self.config.watch_patterns
        for wd in watch_dirs:
            if not wd.exists():
                continue
            for pattern in patterns:
                for f in wd.rglob(pattern):
                    if not f.is_file():
                        continue
                    current_mtime = f.stat().st_mtime
                    if self._file_state.get(f, 0) != current_mtime:
                        self._file_state[f] = current_mtime
                        return True
        return False

    def _parse_failures(self, output: str) -> list[TestFailureEvent]:
        """Parse pytest output for failures."""
        failures = []

        # Pattern for failure header: test_path::test_name FAILED
        failure_pattern = re.compile(
            r"^(.+?::[\w_]+)\s+FAILED$",
            re.MULTILINE,
        )

        # Pattern for error type and message
        error_pattern = re.compile(
            r"(?P<error_type>\w+Error):\s+(?P<error_message>.+?)(?=\n\n|\Z)",
            re.DOTALL,
        )

        for match in failure_pattern.finditer(output):
            test_path = match.group(1)

            # Find the error details after the failure header
            start = match.end()
            error_match = error_pattern.search(output[start:start + 500])

            if error_match:
                error_type = error_match.group("error_type")
                error_message = error_match.group("error_message").strip()
            else:
                error_type = "UnknownError"
                error_message = "Unknown error"

            failure = TestFailureEvent(
                test_path=test_path,
                error_type=error_type,
                error_message=error_message,
                traceback=output[match.start():match.start() + 1000],
            )
            failures.append(failure)

        return failures
