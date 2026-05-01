"""Plugin hot-reloading watcher — monitors plugin directory for changes."""

import hashlib
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from selfheal.config import WatcherConfig
from selfheal.interfaces.watcher import WatcherInterface
from selfheal.plugins.loader import PluginLoader

logger = logging.getLogger(__name__)


class PluginWatcher(WatcherInterface):
    """Hot-reloading watcher: monitors a plugin directory and reloads
    changed modules at runtime.

    Uses ``watchdog`` for efficient file-system event monitoring.
    Falls back to polling if watchdog is not installed.

    Config fields used:
        ``path`` — directory containing plugin ``.py`` files
        ``poll_interval`` — debounce interval (seconds) between change
            detection and reload
    """

    name = "plugin_watcher"

    def __init__(self, config: WatcherConfig):
        self.config = config
        self.plugin_dir = Path(config.path).resolve()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loader = PluginLoader()
        # Debounce: track pending reloads to avoid multiple reloads
        # for rapid successive change events (editor save, etc.)
        self._pending: dict[str, float] = {}
        self._debounce_lock = threading.Lock()
        # Checksum registry for integrity verification
        self._checksums: dict[str, str] = {}  # file_path → sha256 hash
        self._checksums_lock = threading.Lock()

    def start(self, paths: list[str], callback: Callable[[Any], None]) -> None:
        """Start monitoring the plugin directory."""
        if not self.plugin_dir.exists():
            self.plugin_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created plugin directory: {self.plugin_dir}")

        # Initial load of existing plugins
        self._loader.load_from_path(self.plugin_dir)
        # Record baseline checksums for later integrity verification
        self._record_checksums_from_dir(self.plugin_dir)

        self._running = True
        self._thread = threading.Thread(
            target=self._watch_loop,
            args=(callback,),
            daemon=True,
        )
        self._thread.start()
        logger.info(f"PluginWatcher started for: {self.plugin_dir}")

    def stop(self) -> None:
        """Stop monitoring."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("PluginWatcher stopped")

    # ------------------------------------------------------------------
    # Internal watch loop
    # ------------------------------------------------------------------

    def _watch_loop(self, callback: Callable[[Any], None]) -> None:
        """Main monitoring loop — uses watchdog if available, otherwise polling."""
        if self._try_watchdog_loop(callback):
            return

        # Fallback: polling-based detection
        self._polling_loop(callback)

    # ------------------------------------------------------------------
    # Watchdog-based monitoring
    # ------------------------------------------------------------------

    def _try_watchdog_loop(self, callback: Callable[[Any], None]) -> bool:
        """Attempt to use watchdog for file monitoring. Returns True on success."""
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            logger.info(
                "watchdog not installed — falling back to polling. "
                "Install with: pip install selfheal[hotreload]"
            )
            return False

        watcher_self = self

        class _PluginFileHandler(FileSystemEventHandler):
            """Watchdog event handler that schedules plugin reloads."""

            def on_modified(self, event):
                if not event.is_directory:
                    watcher_self.schedule_reload(event.src_path)

            def on_created(self, event):
                if not event.is_directory:
                    watcher_self.schedule_reload(event.src_path)

        handler = _PluginFileHandler()
        observer = Observer()
        observer.schedule(handler, str(self.plugin_dir), recursive=True)
        observer.start()

        logger.info("PluginWatcher using watchdog (efficient file monitoring)")

        try:
            # Keep the thread alive while running; watchdog runs in its own thread
            interval = max(0.5, self.config.poll_interval)
            while self._running:
                time.sleep(interval)
                self._process_pending_reloads(callback)
        finally:
            observer.stop()
            observer.join(timeout=5)

        return True

    # ------------------------------------------------------------------
    # Polling-based fallback
    # ------------------------------------------------------------------

    def _polling_loop(self, callback: Callable[[Any], None]) -> None:
        """Polling-based file change detection (fallback when no watchdog)."""
        file_state: dict[Path, float] = {}
        patterns = self.config.watch_patterns or ["*.py"]
        interval = max(1.0, self.config.poll_interval)

        # Initial snapshot
        self._snapshot_dir(file_state, patterns)

        while self._running:
            time.sleep(interval)
            if not self._running:
                break

            changed = self._detect_changes(file_state, patterns)
            for file_path in changed:
                self._schedule_reload(file_path)

            self._process_pending_reloads(callback)

    def _snapshot_dir(self, file_state: dict[Path, float], patterns: list[str]) -> None:
        """Record mtime for all matching files."""
        for pattern in patterns:
            for f in self.plugin_dir.rglob(pattern):
                if f.is_file():
                    file_state[f] = f.stat().st_mtime

    def _detect_changes(
        self, file_state: dict[Path, float], patterns: list[str]
    ) -> list[Path]:
        """Return list of changed files since last snapshot."""
        changed = []
        seen: set[Path] = set()

        for pattern in patterns:
            for f in self.plugin_dir.rglob(pattern):
                if not f.is_file():
                    continue
                seen.add(f)
                current_mtime = f.stat().st_mtime
                if file_state.get(f, 0) != current_mtime:
                    file_state[f] = current_mtime
                    changed.append(f)

        # Remove deleted files from state
        for f in list(file_state):
            if f not in seen:
                del file_state[f]

        return changed

    # ------------------------------------------------------------------
    # Debounced reload
    # ------------------------------------------------------------------

    def schedule_reload(self, file_path: str) -> None:
        """Called by watchdog handler when a file changes."""
        self._schedule_reload(file_path)

    def _schedule_reload(self, file_path: str | Path) -> None:
        """Schedule a plugin file for reload (debounced)."""
        key = str(file_path)
        with self._debounce_lock:
            self._pending[key] = time.time()

    def _process_pending_reloads(self, callback: Callable[[Any], None]) -> None:
        """Reload plugins whose debounce period has elapsed."""
        now = time.time()
        debounce = max(0.5, self.config.poll_interval)

        to_process: list[str] = []
        with self._debounce_lock:
            for key, ts in list(self._pending.items()):
                if now - ts >= debounce:
                    to_process.append(key)
                    del self._pending[key]

        for file_path in to_process:
            path = Path(file_path)
            if not path.exists():
                logger.debug(f"Plugin file removed, skipping: {path}")
                continue
            success = self._loader.load_or_reload_file(path, self.plugin_dir)
            if success:
                logger.info(f"Hot-reloaded plugin: {path.name}")
                self._record_checksum(path)
            # Note: callback is for failure events; plugin reloads don't produce failures

    # ------------------------------------------------------------------
    # Integrity verification
    # ------------------------------------------------------------------

    def check_integrity(self) -> dict[str, list[str]]:
        """Verify integrity of all tracked plugin files.

        Computes the current SHA256 checksum of every plugin file that has
        been loaded or hot-reloaded and compares it against the stored
        reference checksum.  A mismatch means the file has been tampered
        with or corrupted since it was last loaded.

        Returns:
            dict with three keys::

                {
                    "ok":        ["path/to/intact_plugin.py", ...],
                    "modified":  ["path/to/tampered_plugin.py", ...],
                    "missing":   ["path/to/deleted_plugin.py", ...],
                }

            When both ``modified`` and ``missing`` are empty the registry
            is fully intact.
        """
        result: dict[str, list[str]] = {"modified": [], "missing": [], "ok": []}

        with self._checksums_lock:
            for file_path_str, expected_checksum in list(self._checksums.items()):
                path = Path(file_path_str)

                if not path.exists():
                    result["missing"].append(file_path_str)
                    continue

                current_checksum = self._compute_checksum(path)
                if current_checksum != expected_checksum:
                    result["modified"].append(file_path_str)
                else:
                    result["ok"].append(file_path_str)

        return result

    # ------------------------------------------------------------------
    # Checksum helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_checksum(file_path: Path) -> str:
        """Compute SHA256 hex digest of *file_path* contents."""
        sha = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha.update(chunk)
        return sha.hexdigest()

    def _record_checksum(self, file_path: Path) -> str:
        """Compute and store checksum for *file_path*.  Returns the checksum."""
        checksum = self._compute_checksum(file_path)
        with self._checksums_lock:
            self._checksums[str(file_path)] = checksum
        return checksum

    def _record_checksums_from_dir(self, directory: Path) -> None:
        """Record checksums for every *.py* file in *directory*.

        Called after the initial ``load_from_path`` so integrity can be
        verified later.
        """
        if not directory.exists() or not directory.is_dir():
            return
        patterns = self.config.watch_patterns or ["*.py"]
        for pattern in patterns:
            for f in directory.rglob(pattern):
                if f.is_file() and f.suffix == ".py" and not f.name.startswith("_"):
                    self._record_checksum(f)




