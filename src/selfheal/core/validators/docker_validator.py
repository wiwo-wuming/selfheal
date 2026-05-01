"""Docker validator implementation."""

import logging
import time
from pathlib import Path
from typing import Optional

from selfheal.config import ValidatorConfig, DockerConfig
from selfheal.events import PatchEvent, ValidationEvent
from selfheal.interfaces.validator import ValidatorInterface

logger = logging.getLogger(__name__)


class DockerValidator(ValidatorInterface):
    """Validates patches using Docker containers."""

    def __init__(self, config: ValidatorConfig):
        self.config = config
        self.docker_config = config.docker or DockerConfig()
        self.timeout = config.timeout
        self._client = None

    name = "docker"

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
        """Validate a patch inside a Docker container."""
        start_time = time.time()
        test_path = patch.classification_event.original_event.test_path

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
