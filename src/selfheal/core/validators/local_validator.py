"""Local validator implementation."""

import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from selfheal.config import ValidatorConfig
from selfheal.events import PatchEvent, ValidationEvent
from selfheal.interfaces.validator import ValidatorInterface

logger = logging.getLogger(__name__)


class LocalValidator(ValidatorInterface):
    """Validates patches by running tests locally."""

    def __init__(self, config: ValidatorConfig):
        self.config = config
        self.timeout = config.timeout
        self.venv_path = config.venv_path

    name = "local"

    def validate(self, patch: PatchEvent) -> ValidationEvent:
        """Validate a patch by running the test."""
        start_time = time.time()
        test_path = patch.classification_event.original_event.test_path

        logger.info(f"Validating patch for: {test_path}")

        try:
            # Determine the test command
            cmd = self._build_test_command(test_path)

            # Run the test
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=self._get_working_dir(),
            )

            duration = time.time() - start_time
            passed = result.returncode == 0

            if passed:
                logger.info(f"Validation passed in {duration:.2f}s")
                return ValidationEvent(
                    patch_event=patch,
                    result="passed",
                    test_output=result.stdout + result.stderr,
                    duration=duration,
                )
            else:
                logger.warning(f"Validation failed in {duration:.2f}s")
                return ValidationEvent(
                    patch_event=patch,
                    result="failed",
                    test_output=result.stdout + result.stderr,
                    duration=duration,
                    error_message=result.stderr,
                )

        except subprocess.TimeoutExpired:
            duration = time.time() - start_time
            logger.error(f"Validation timed out after {duration:.2f}s")
            return ValidationEvent(
                patch_event=patch,
                result="error",
                duration=duration,
                error_message=f"Validation timed out after {self.timeout}s",
            )
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Validation error: {e}")
            return ValidationEvent(
                patch_event=patch,
                result="error",
                duration=duration,
                error_message=str(e),
            )

    def _build_test_command(self, test_path: str) -> list[str]:
        """Build the test command."""
        cmd = ["pytest", "-v", "--tb=short", test_path]

        if self.venv_path:
            # Cross-platform venv python detection
            venv_base = Path(self.venv_path)
            if sys.platform == "win32":
                venv_python = venv_base / "Scripts" / "python.exe"
            else:
                venv_python = venv_base / "bin" / "python"
            if venv_python.exists():
                cmd = [str(venv_python), "-m", "pytest", "-v", "--tb=short", test_path]

        return cmd

    def _get_working_dir(self) -> Optional[Path]:
        """Get the working directory for tests."""
        if self.venv_path:
            venv_path = Path(self.venv_path)
            if venv_path.exists():
                return venv_path.parent  # Project root

        return Path.cwd()
