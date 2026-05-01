"""Docker validator implementation with graceful degradation."""

import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from selfheal.config import ValidatorConfig, DockerConfig
from selfheal.events import PatchEvent, ValidationEvent
from selfheal.interfaces.validator import ValidatorInterface

logger = logging.getLogger(__name__)


class DockerValidator(ValidatorInterface):
    """Validates patches using Docker containers.

    Falls back gracefully when Docker is not installed, providing a clear
    diagnostic message and an ``"error"`` result rather than crashing.
    """

    def __init__(self, config: ValidatorConfig):
        self.config = config
        self.docker_config = config.docker or DockerConfig()
        self.timeout = config.timeout
        self._client = None
        self._docker_available: Optional[bool] = None  # cached check

    name = "docker"

    def _check_docker_available(self) -> bool:
        """Check whether Docker is installed and reachable.

        Cached after first call to avoid repeated subprocess invocations.

        The check is skipped when:
        - ``DockerValidator._test_mode = True`` (set by tests that mock Docker)
        - ``SELFHEAL_SKIP_DOCKER_CHECK=1`` environment variable
        """
        if self._docker_available is not None:
            return self._docker_available

        # Allow tests and CI to bypass the real Docker check
        if getattr(DockerValidator, "_test_mode", False):
            logger.debug("Docker check bypassed (test mode)")
            self._docker_available = True
            return True

        if os.environ.get("SELFHEAL_SKIP_DOCKER_CHECK") == "1":
            logger.debug("Docker check bypassed (SELFHEAL_SKIP_DOCKER_CHECK=1)")
            self._docker_available = True
            return True

        # Check 1: docker CLI
        if shutil.which("docker") is None:
            logger.warning(
                "Docker CLI not found on PATH. "
                "Install Docker Desktop from https://www.docker.com/products/docker-desktop/"
            )
            self._docker_available = False
            return False

        # Check 2: docker daemon is running
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                logger.warning(
                    "Docker daemon not running. Start Docker Desktop and retry.\n"
                    "  error: %s", result.stderr.strip()[:200]
                )
                self._docker_available = False
                return False
        except subprocess.TimeoutExpired:
            logger.warning("Docker daemon check timed out — is Docker Desktop running?")
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
                raise ImportError("docker package not installed. Run: pip install selfheal[docker]")

        return self._client

    def validate(self, patch: PatchEvent) -> ValidationEvent:
        """Validate a patch inside a Docker container.

        If Docker is not available, returns an ``"error"`` result with
        a clear diagnostic message instead of crashing.
        """
        start_time = time.time()
        test_path = patch.classification_event.original_event.test_path

        # Graceful degradation: check Docker availability first
        if not self._check_docker_available():
            return ValidationEvent(
                patch_event=patch,
                result="error",
                duration=0.0,
                error_message=(
                    "Docker is not available. "
                    "Install Docker Desktop (https://www.docker.com/products/docker-desktop/) "
                    "or switch validator to 'local'."
                ),
            )

        logger.info(f"Validating patch in Docker: {test_path}")

        container = None
        try:
            client = self._get_client()

            # Build the test command
            cmd = ["pytest", "-v", "--tb=short", test_path]
            workdir = "/workspace"

            # Run container with volume mount for project code
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

            # Wait for completion with timeout
            try:
                result = container.wait(timeout=self.docker_config.timeout)
            except Exception:
                # Client-side timeout — container may still be running;
                # force-stop it before raising to prevent resource leaks.
                try:
                    container.stop(timeout=10)
                except Exception:
                    pass
                raise

            # Get logs
            logs = container.logs().decode("utf-8")

            duration = time.time() - start_time
            passed = result["StatusCode"] == 0

            if passed:
                logger.info(f"Docker validation passed in {duration:.2f}s")
                return ValidationEvent(
                    patch_event=patch,
                    result="passed",
                    test_output=logs,
                    duration=duration,
                )
            else:
                logger.warning(f"Docker validation failed in {duration:.2f}s")
                return ValidationEvent(
                    patch_event=patch,
                    result="failed",
                    test_output=logs,
                    duration=duration,
                    error_message=logs,
                )

        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Docker validation error: {e}")
            return ValidationEvent(
                patch_event=patch,
                result="error",
                duration=duration,
                error_message=str(e),
            )
        finally:
            # Always clean up the container to prevent resource leaks
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    logger.warning(f"Failed to remove Docker container")
