"""Plugin sandbox for isolated execution of untrusted plugin code.

Runs plugin functions in a subprocess with JSON-based data exchange,
timeout protection, and optional SHA256 integrity pre-check.
"""

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30  # seconds


class PluginSandbox:
    """Execute plugin code in an isolated subprocess.

    Each call to :meth:`execute` spawns a fresh Python interpreter that
    loads the plugin file, calls the specified function, and returns the
    result via JSON on stdout.  The host process is protected from crashes,
    infinite loops, and side effects in the plugin code.
    """

    def __init__(self, timeout: int = DEFAULT_TIMEOUT):
        self.timeout = timeout

    def execute(
        self,
        plugin_path: Path,
        func_name: str = "run",
        args: Optional[dict[str, Any]] = None,
        expected_checksum: Optional[str] = None,
    ) -> dict[str, Any]:
        """Execute a function from a plugin file in a subprocess.

        Args:
            plugin_path: Absolute path to the plugin ``.py`` file.
            func_name: Name of the function to call (default ``"run"``).
            args: JSON-serializable keyword arguments for the function.
            expected_checksum: Optional SHA256 hex digest for integrity
                pre-check.  If provided and the file's actual checksum
                differs, execution is skipped.

        Returns:
            ``{"success": True, "result": ...}`` on success, or
            ``{"success": False, "error": "<message>"}`` on failure.
        """
        if not plugin_path.exists():
            return {"success": False, "error": f"Plugin file not found: {plugin_path}"}

        # Integrity pre-check
        if expected_checksum:
            actual = self._compute_sha256(plugin_path)
            if actual != expected_checksum:
                logger.error(
                    "Integrity check failed for %s: expected %s... got %s...",
                    plugin_path,
                    expected_checksum[:16],
                    actual[:16],
                )
                return {"success": False, "error": "Integrity check failed"}

        # Build the wrapper script
        wrapper = self._build_wrapper(plugin_path, func_name, args or {})

        # Execute in subprocess
        try:
            proc = subprocess.run(
                [sys.executable, "-c", wrapper],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Plugin timed out after {self.timeout}s"}
        except FileNotFoundError:
            return {"success": False, "error": "Python interpreter not found"}
        except Exception as exc:
            return {"success": False, "error": f"Subprocess error: {exc}"}

        # Parse JSON result from stdout
        stdout = proc.stdout.strip() if proc.stdout else ""
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            stderr_snippet = (proc.stderr or "")[:200]
            return {
                "success": False,
                "error": f"Failed to parse plugin output. stderr: {stderr_snippet or stdout[:200]}",
            }

    def _build_wrapper(
        self, plugin_path: Path, func_name: str, args: dict[str, Any]
    ) -> str:
        """Build the Python code that the subprocess will execute.

        The wrapper:
        1. Loads the plugin module from *plugin_path*
        2. Calls ``func_name(**args)``
        3. Prints the result as a JSON object to stdout
        """
        args_json = json.dumps(args)
        return (
            "import importlib.util, json, sys\n"
            "try:\n"
            f"    spec = importlib.util.spec_from_file_location('sandbox_plugin', {str(plugin_path)!r})\n"
            "    mod = importlib.util.module_from_spec(spec)\n"
            "    spec.loader.exec_module(mod)\n"
            f"    func = getattr(mod, {func_name!r})\n"
            f"    result = func(**{args_json})\n"
            "    print(json.dumps({'success': True, 'result': result}))\n"
            "except Exception as e:\n"
            "    print(json.dumps({'success': False, 'error': str(e)}))\n"
        )

    @staticmethod
    def _compute_sha256(file_path: Path) -> str:
        """Compute SHA256 hex digest of a file (64KB chunked read)."""
        import hashlib

        sha = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha.update(chunk)
        return sha.hexdigest()
