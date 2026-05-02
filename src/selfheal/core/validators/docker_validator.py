"""Docker validator with sandbox isolation and graceful degradation.

Two modes:
- **sandbox** (default): Copy project to temp dir, apply patch there, run tests in
  an isolated container. Host files are NEVER touched during validation.
- **direct**: Mount host dir read-write into container (fast but modifies host).
"""

import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from selfheal.config import ValidatorConfig, DockerConfig
from selfheal.events import PatchEvent, ValidationEvent
from selfheal.interfaces.validator import ValidatorInterface

logger = logging.getLogger(__name__)


class DockerValidator(ValidatorInterface):
    """Validates patches using Docker containers.

    When ``sandbox=True`` (default), the validator:
    1. Copies the project into a temporary directory
    2. Applies the patch inside the temp copy
    3. Runs pytest in an isolated Docker container
    4. Returns pass/fail **without touching host files**
    5. Cleans up the temp directory

    When ``sandbox=False``, mounts the host working directory into the
    container directly (faster but the patch must already be applied).

    Falls back gracefully when Docker is not installed.
    """

    def __init__(self, config: ValidatorConfig):
        self.config = config
        self.docker_config = config.docker or DockerConfig()
        self.timeout = config.timeout
        self._client = None
        self._docker_available: Optional[bool] = None  # cached check

    name = "docker"

    # ---------- Docker availability checks ----------

    def _check_docker_available(self) -> bool:
        """Check whether Docker is installed and reachable (cached)."""
        if self._docker_available is not None:
            return self._docker_available

        if getattr(DockerValidator, "_test_mode", False):
            logger.debug("Docker check bypassed (test mode)")
            self._docker_available = True
            return True

        if os.environ.get("SELFHEAL_SKIP_DOCKER_CHECK") == "1":
            logger.debug("Docker check bypassed (SELFHEAL_SKIP_DOCKER_CHECK=1)")
            self._docker_available = True
            return True

        if shutil.which("docker") is None:
            logger.warning(
                "Docker CLI not found. Install Docker Desktop: "
                "https://www.docker.com/products/docker-desktop/"
            )
            self._docker_available = False
            return False

        try:
            result = subprocess.run(
                ["docker", "info"], capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                logger.warning("Docker daemon not running: %s", result.stderr.strip()[:200])
                self._docker_available = False
                return False
        except subprocess.TimeoutExpired:
            logger.warning("Docker daemon check timed out")
            self._docker_available = False
            return False
        except Exception as e:
            logger.warning("Unexpected error checking Docker: %s", e)
            self._docker_available = False
            return False

        logger.info("Docker is available and running")
        self._docker_available = True
        return True

    def _get_client(self):
        """Get or create Docker client."""
        if self._client is None:
            try:
                import docker
                self._client = docker.from_env()
            except ImportError:
                raise ImportError(
                    "docker package not installed. Run: pip install selfheal[docker]"
                )
        return self._client

    # ---------- Validation entry point ----------

    def validate(self, patch: PatchEvent) -> ValidationEvent:
        """Validate a patch inside a Docker container.

        Routing:
        - Docker unavailable → error result with diagnostic
        - sandbox=True → isolated temp-copy validation
        - sandbox=False → direct host mount validation
        """
        start_time = time.time()
        test_path = patch.classification_event.original_event.test_path

        if not self._check_docker_available():
            return ValidationEvent(
                patch_event=patch,
                result="error",
                duration=0.0,
                error_message=(
                    "Docker is not available. Install Docker Desktop "
                    "(https://www.docker.com/products/docker-desktop/) "
                    "or use --validator local."
                ),
            )

        if getattr(self.docker_config, "sandbox", True):
            return self._validate_sandbox(patch, test_path, start_time)
        else:
            return self._validate_direct(patch, test_path, start_time)

    # ---------- Sandbox mode: temp copy + isolated container ----------

    def _validate_sandbox(
        self, patch: PatchEvent, test_path: str, start_time: float
    ) -> ValidationEvent:
        """Validate in an isolated temp copy — host files untouched."""
        temp_dir = None
        try:
            # 1. Create temp directory and copy project
            temp_dir = Path(tempfile.mkdtemp(prefix="selfheal_sandbox_"))
            logger.info("Sandbox: copying project to %s ...", temp_dir)
            self._copy_project(temp_dir)

            # 2. Apply patch in the temp copy
            target_file = patch.target_file
            if target_file:
                # Make target relative to project root
                rel_target = self._make_relative(target_file)
                sandbox_target = temp_dir / rel_target
                if sandbox_target.exists():
                    self._apply_patch_to_file(sandbox_target, patch.patch_content)
                    logger.info("Sandbox: patch applied to %s", sandbox_target)
                else:
                    logger.warning(
                        "Sandbox: target file %s not in project copy", rel_target
                    )

            # 3. Run tests in Docker with temp dir mounted
            cmd = ["pytest", "-v", "--tb=short", test_path]
            workdir = "/workspace"

            client = self._get_client()
            container = client.containers.run(
                self.docker_config.image,
                command=" ".join(cmd),
                detach=True,
                network=self.docker_config.network,
                stdout=True,
                stderr=True,
                volumes={str(temp_dir): {"bind": workdir, "mode": "rw"}},
                working_dir=workdir,
            )

            # Wait for completion
            try:
                result = container.wait(timeout=self.docker_config.timeout)
            except Exception:
                try:
                    container.stop(timeout=10)
                except Exception:
                    pass
                raise

            logs = container.logs().decode("utf-8", errors="replace")
            duration = time.time() - start_time
            passed = result["StatusCode"] == 0

            if passed:
                logger.info("Sandbox validation PASSED in %.2fs", duration)
            else:
                logger.warning("Sandbox validation FAILED in %.2fs", duration)

            return ValidationEvent(
                patch_event=patch,
                result="passed" if passed else "failed",
                test_output=logs,
                duration=duration,
                error_message="" if passed else logs,
            )

        except Exception as e:
            duration = time.time() - start_time
            logger.error("Sandbox validation error: %s", e)
            return ValidationEvent(
                patch_event=patch,
                result="error",
                duration=duration,
                error_message=str(e),
            )
        finally:
            # Clean up temp directory
            if temp_dir and temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    logger.debug("Sandbox: cleaned up %s", temp_dir)
                except Exception:
                    logger.warning("Failed to clean up sandbox temp dir")

    # ---------- Direct mode: host mount (legacy behaviour) ----------

    def _validate_direct(
        self, patch: PatchEvent, test_path: str, start_time: float
    ) -> ValidationEvent:
        """Validate directly on host via Docker mount (legacy mode)."""
        logger.info("Validating patch in Docker (direct mode): %s", test_path)

        container = None
        try:
            client = self._get_client()
            cmd = ["pytest", "-v", "--tb=short", test_path]
            workdir = "/workspace"

            container = client.containers.run(
                self.docker_config.image,
                command=" ".join(cmd),
                detach=True,
                network=self.docker_config.network,
                stdout=True,
                stderr=True,
                volumes={str(Path.cwd()): {"bind": workdir, "mode": "rw"}},
                working_dir=workdir,
            )

            try:
                result = container.wait(timeout=self.docker_config.timeout)
            except Exception:
                try:
                    container.stop(timeout=10)
                except Exception:
                    pass
                raise

            logs = container.logs().decode("utf-8", errors="replace")
            duration = time.time() - start_time
            passed = result["StatusCode"] == 0

            return ValidationEvent(
                patch_event=patch,
                result="passed" if passed else "failed",
                test_output=logs,
                duration=duration,
                error_message="" if passed else logs,
            )

        except Exception as e:
            duration = time.time() - start_time
            logger.error("Docker validation error: %s", e)
            return ValidationEvent(
                patch_event=patch,
                result="error",
                duration=duration,
                error_message=str(e),
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    logger.warning("Failed to remove Docker container")

    # ---------- Helpers ----------

    @staticmethod
    def _copy_project(dest: Path) -> None:
        """Copy the current project into dest, skipping VCS/venv/cache dirs."""
        cwd = Path.cwd()
        skip_patterns = {
            ".git", "__pycache__", ".pytest_cache", ".selfheal",
            "venv", ".venv", "env", "node_modules", ".tox",
            "*.pyc", ".DS_Store", ".mypy_cache", ".ruff_cache",
        }

        def _ignore(src_dir, names):
            ignored = set()
            for name in names:
                if name in skip_patterns:
                    ignored.add(name)
                elif name.endswith((".pyc", ".pyo")):
                    ignored.add(name)
            # Also skip hidden dirs except .github
            for name in names:
                if name.startswith(".") and name not in (".github",):
                    ignored.add(name)
            return ignored

        shutil.copytree(str(cwd), str(dest), ignore=_ignore, dirs_exist_ok=True)

    @staticmethod
    def _make_relative(file_path: str) -> str:
        """Convert an absolute path to a path relative to cwd."""
        cwd = str(Path.cwd())
        fp = str(Path(file_path).resolve())
        if fp.startswith(cwd):
            rel = fp[len(cwd):].lstrip(os.sep).lstrip("/")
            return rel or "."
        # Fallback: just use the basename
        return Path(file_path).name

    @staticmethod
    def _apply_patch_to_file(target: Path, patch_content: str) -> None:
        """Apply a unified-diff or replacement patch to a single file.

        This is a lightweight inline patcher for the sandbox temp copy.
        """
        if not target.exists():
            return

        # Try unified diff first
        if any(
            line.startswith(("--- ", "+++ ", "@@ ", "diff --git"))
            for line in patch_content.strip().split("\n")[:10]
        ):
            # Write patch to temp file and use system patch command
            import tempfile as tf
            try:
                with tf.NamedTemporaryFile(
                    mode="w", suffix=".patch", delete=False, encoding="utf-8"
                ) as tmp:
                    tmp.write(patch_content)
                    tmp_path = tmp.name

                subprocess.run(
                    ["patch", "-p0", str(target), "-i", tmp_path],
                    capture_output=True,
                    timeout=30,
                )
                Path(tmp_path).unlink(missing_ok=True)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                # patch command not available — apply manually
                DockerValidator._apply_diff_manually(target, patch_content)
        else:
            # Full replacement
            target.write_text(patch_content, encoding="utf-8")

    @staticmethod
    def _apply_diff_manually(target: Path, diff_content: str) -> None:
        """Fallback manual unified-diff application."""
        import re

        original = target.read_text(encoding="utf-8")
        original_lines = original.splitlines(keepends=True)
        diff_lines = diff_content.splitlines(keepends=True)

        hunk_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
        result = list(original_lines)
        offset = 0
        i = 0

        while i < len(diff_lines):
            line = diff_lines[i]
            if line.startswith(("---", "+++", "diff ")):
                i += 1
                continue

            match = hunk_re.match(line)
            if not match:
                i += 1
                continue

            old_start = int(match.group(1))
            old_count = int(match.group(2)) if match.group(2) else 1
            i += 1

            additions = []
            remove_count = 0
            line_idx = 0

            while i < len(diff_lines) and line_idx < old_count + 100:
                hline = diff_lines[i]
                if hline.startswith("@@") or hline.startswith("diff ") or hline.startswith("--- "):
                    break
                if hline.startswith("+"):
                    additions.append(hline[1:])
                elif hline.startswith("-"):
                    remove_count += 1
                elif hline.startswith(" "):
                    additions.append(hline[1:])
                elif not hline.startswith("\\"):
                    additions.append(hline)
                i += 1
                line_idx += 1

            insert_pos = old_start - 1 + offset
            result[insert_pos:insert_pos + old_count] = additions
            offset += len(additions) - old_count

        target.write_text("".join(result), encoding="utf-8")
