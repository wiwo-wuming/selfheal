"""Integration tests for Docker validator (mock subprocess/Docker SDK)."""

import os
from unittest.mock import MagicMock, patch

import pytest

from selfheal.config import DockerConfig, ValidatorConfig
from selfheal.core.validators.docker_validator import DockerValidator
from selfheal.events import (
    ClassificationEvent,
    ErrorSeverity,
    PatchEvent,
    TestFailureEvent,
)


# ---------------------------------------------------------------------------
# Module-level setup: bypass real Docker check for all tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _bypass_docker_check(monkeypatch):
    """Bypass real Docker availability check — tests mock Docker SDK."""
    DockerValidator._test_mode = True
    yield
    DockerValidator._test_mode = False


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_patch():
    """Build a PatchEvent for validator testing."""
    failure = TestFailureEvent(
        test_path="tests/test_math.py::test_add",
        error_type="AssertionError",
        error_message="assert 2 == 3",
        traceback="E   assert 2 == 3",
    )
    classification = ClassificationEvent(
        original_event=failure,
        category="assertion",
        severity=ErrorSeverity.HIGH,
        confidence=0.9,
    )
    return PatchEvent(
        classification_event=classification,
        patch_id="patch-001",
        patch_content="def test_add():\n    assert 1 + 1 == 2\n",
        generator="template",
    )


@pytest.fixture
def docker_config():
    return ValidatorConfig(
        type="docker",
        timeout=120,
        docker=DockerConfig(image="python:3.11-slim", timeout=60),
    )


# ---------------------------------------------------------------------------
# DockerValidator tests
# ---------------------------------------------------------------------------

class TestDockerValidator:
    """Tests for Docker container-based validator."""

    def test_validator_name(self, docker_config):
        validator = DockerValidator(docker_config)
        assert validator.name == "docker"

    def test_default_docker_config(self):
        """Uses default DockerConfig when none provided."""
        config = ValidatorConfig(type="docker", docker=None)
        validator = DockerValidator(config)
        assert validator.docker_config.image == "python:3.11-slim"
        assert validator.docker_config.timeout == 600

    @patch.object(DockerValidator, "_get_client")
    def test_validate_passed(self, mock_get_client, docker_config, sample_patch):
        """validate() returns 'passed' when container exits 0 (direct mode)."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_container = MagicMock()
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.return_value = (
            b"tests/test_math.py::test_add PASSED\n1 passed in 0.05s\n"
        )
        mock_client.containers.run.return_value = mock_container

        # Use direct mode (sandbox=False) to test container lifecycle
        docker_config.docker.sandbox = False
        validator = DockerValidator(docker_config)
        result = validator.validate(sample_patch)

        assert result.result == "passed"
        assert "PASSED" in result.test_output
        assert result.duration > 0

        # Verify container was cleaned up (direct mode calls remove in finally)
        mock_container.remove.assert_called_once_with(force=True)

        # Verify correct image and command were used
        mock_client.containers.run.assert_called_once()
        # image is the first positional argument
        assert mock_client.containers.run.call_args[0][0] == "python:3.11-slim"
        assert "pytest" in mock_client.containers.run.call_args[1]["command"]

    @patch.object(DockerValidator, "_get_client")
    def test_validate_failed(self, mock_get_client, docker_config, sample_patch):
        """validate() returns 'failed' when container exits non-zero (direct mode)."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_container = MagicMock()
        mock_container.wait.return_value = {"StatusCode": 1}
        mock_container.logs.return_value = (
            b"tests/test_math.py::test_add FAILED\n"
            b"assert 2 == 3\n1 failed in 0.03s\n"
        )
        mock_client.containers.run.return_value = mock_container

        docker_config.docker.sandbox = False
        validator = DockerValidator(docker_config)
        result = validator.validate(sample_patch)

        assert result.result == "failed"
        assert "FAILED" in result.test_output
        assert result.error_message == result.test_output
        mock_container.remove.assert_called_once_with(force=True)

    @patch.object(DockerValidator, "_get_client")
    def test_validate_timeout(self, mock_get_client, docker_config, sample_patch):
        """validate() returns 'error' when container wait times out (direct mode)."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_container = MagicMock()
        mock_container.wait.side_effect = Exception("Read timed out")
        mock_client.containers.run.return_value = mock_container

        docker_config.docker.sandbox = False
        validator = DockerValidator(docker_config)
        result = validator.validate(sample_patch)

        assert result.result == "error"
        # Container should be stopped on timeout, then removed in finally
        mock_container.stop.assert_called_once()
        mock_container.remove.assert_called_once_with(force=True)

    @patch.object(DockerValidator, "_get_client")
    def test_validate_docker_error(self, mock_get_client, sample_patch):
        """validate() returns 'error' when Docker client fails."""
        mock_get_client.side_effect = Exception("Docker daemon not running")

        config = ValidatorConfig(
            type="docker",
            timeout=60,
            docker=DockerConfig(image="python:3.11-slim", timeout=30),
        )
        validator = DockerValidator(config)
        result = validator.validate(sample_patch)

        assert result.result == "error"
        assert "Docker daemon not running" in result.error_message

    @patch.object(DockerValidator, "_get_client")
    def test_validate_container_remove_error(
        self, mock_get_client, docker_config, sample_patch
    ):
        """Cleanup failure does not mask the validation result (direct mode)."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_container = MagicMock()
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.return_value = b"1 passed\n"
        mock_container.remove.side_effect = Exception("Cannot remove")
        mock_client.containers.run.return_value = mock_container

        docker_config.docker.sandbox = False
        validator = DockerValidator(docker_config)
        result = validator.validate(sample_patch)

        # Result should still be 'passed' — cleanup failure is logged, not raised
        assert result.result == "passed"

    @patch.object(DockerValidator, "_get_client")
    def test_uses_custom_network(self, mock_get_client, sample_patch):
        """validate() passes network config to container run."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_container = MagicMock()
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.return_value = b"ok"
        mock_client.containers.run.return_value = mock_container

        config = ValidatorConfig(
            type="docker",
            timeout=60,
            docker=DockerConfig(
                image="python:3.10", timeout=30, network="test-net"
            ),
        )
        validator = DockerValidator(config)
        validator.validate(sample_patch)

        run_kwargs = mock_client.containers.run.call_args[1]
        assert run_kwargs["network"] == "test-net"
        # image is the first positional argument
        assert mock_client.containers.run.call_args[0][0] == "python:3.10"

    @patch.object(DockerValidator, "_get_client")
    def test_includes_volume_mount(
        self, mock_get_client, docker_config, sample_patch
    ):
        """validate() mounts current directory as /workspace."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_container = MagicMock()
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.return_value = b"ok"
        mock_client.containers.run.return_value = mock_container

        validator = DockerValidator(docker_config)
        validator.validate(sample_patch)

        run_kwargs = mock_client.containers.run.call_args[1]
        assert "volumes" in run_kwargs
        assert run_kwargs["working_dir"] == "/workspace"
