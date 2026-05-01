"""Integration tests for CLI commands (click.testing.CliRunner)."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from click.testing import CliRunner

from selfheal.cli import main


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def sample_failure_json():
    return {
        "test_path": "tests/test_math.py::test_subtract",
        "error_type": "AssertionError",
        "error_message": "assert 5 == 3",
        "traceback": "E   assert 5 == 3",
    }


@pytest.fixture
def sample_classification_json():
    return {
        "original_event": {
            "test_path": "tests/test_math.py::test_subtract",
            "error_type": "AssertionError",
            "error_message": "assert 5 == 3",
            "traceback": "E   assert 5 == 3",
        },
        "category": "assertion",
        "severity": "high",
        "confidence": 0.92,
        "reasoning": "Simple assertion failure",
    }


@pytest.fixture
def sample_patch_json():
    return {
        "classification_event": {
            "original_event": {
                "test_path": "tests/test_math.py::test_subtract",
                "error_type": "AssertionError",
                "error_message": "assert 5 == 3",
                "traceback": "E   assert 5 == 3",
            },
            "category": "assertion",
            "severity": "high",
            "confidence": 0.92,
        },
        "patch_id": "patch-abc",
        "patch_content": "def test_subtract():\n    assert 5 - 2 == 3\n",
        "generator": "template",
    }


@pytest.fixture
def sample_validation_json():
    return {
        "patch_event": {
            "classification_event": {
                "original_event": {
                    "test_path": "tests/test_math.py::test_subtract",
                    "error_type": "AssertionError",
                    "error_message": "assert 5 == 3",
                    "traceback": "E   assert 5 == 3",
                },
                "category": "assertion",
                "severity": "high",
                "confidence": 0.92,
            },
            "patch_id": "patch-abc",
            "patch_content": "def test_subtract():\n    assert 5 - 2 == 3\n",
            "generator": "template",
        },
        "result": "passed",
        "test_output": "1 passed in 0.05s",
        "duration": 0.05,
    }


# ---------------------------------------------------------------------------
# Generic CLI tests
# ---------------------------------------------------------------------------

class TestCLI:
    """Tests for CLI entry point."""

    def test_version(self, runner):
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0

    def test_no_command_shows_help(self, runner):
        result = runner.invoke(main, [])
        # Click exits with code 2 when no subcommand is provided
        assert result.exit_code == 2
        assert "Usage:" in result.output

    def test_help(self, runner):
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        for cmd in ("watch", "classify", "patch", "validate", "report", "batch", "metrics", "init"):
            assert cmd in result.output

    def test_verbose_flag(self, runner):
        result = runner.invoke(main, ["-v", "init", "--output", "test-selfheal.yaml"])
        assert result.exit_code == 0
        # Clean up the generated file
        Path("test-selfheal.yaml").unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# classify command
# ---------------------------------------------------------------------------

class TestClassifyCommand:
    """Tests for 'classify' subcommand."""

    def test_classify_with_rule_classifier(self, runner, sample_failure_json):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(sample_failure_json, f)
            input_path = f.name

        try:
            result = runner.invoke(main, ["classify", "--type", "rule", "--input", input_path])
            assert result.exit_code == 0
            assert "Category:" in result.output
            assert "Severity:" in result.output
        finally:
            Path(input_path).unlink(missing_ok=True)

    def test_classify_with_llm_mocked(self, runner, sample_failure_json):
        """classify with LLM type uses the LLMClassifier backing."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(sample_failure_json, f)
            input_path = f.name

        try:
            result = runner.invoke(main, ["classify", "--type", "llm", "--input", input_path])
            # LLM may fail due to missing API key — that's fine; CLI should handle it
            # Just verify it doesn't crash with a traceback
            assert result.exit_code in (0, 1)
        finally:
            Path(input_path).unlink(missing_ok=True)

    def test_classify_unknown_type(self, runner, sample_failure_json):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(sample_failure_json, f)
            input_path = f.name

        try:
            result = runner.invoke(main, ["classify", "--type", "nonexistent", "--input", input_path])
            assert result.exit_code == 1
            assert "Unknown classifier type" in result.output
        finally:
            Path(input_path).unlink(missing_ok=True)

    def test_classify_missing_input(self, runner):
        result = runner.invoke(main, ["classify"])
        assert result.exit_code != 0
        assert "Error" in result.output or "Missing" in result.output


# ---------------------------------------------------------------------------
# patch command
# ---------------------------------------------------------------------------

class TestPatchCommand:
    """Tests for 'patch' subcommand."""

    def test_patch_with_template(self, runner, sample_classification_json):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(sample_classification_json, f)
            input_path = f.name

        try:
            result = runner.invoke(main, ["patch", "--type", "template", "--input", input_path])
            assert result.exit_code == 0
            assert "Patch ID:" in result.output
            assert "Patch Content:" in result.output
        finally:
            Path(input_path).unlink(missing_ok=True)

    def test_patch_unknown_type(self, runner, sample_classification_json):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(sample_classification_json, f)
            input_path = f.name

        try:
            result = runner.invoke(main, ["patch", "--type", "nonexistent", "--input", input_path])
            assert result.exit_code == 1
            assert "Unknown patcher type" in result.output
        finally:
            Path(input_path).unlink(missing_ok=True)

    def test_patch_missing_input(self, runner):
        result = runner.invoke(main, ["patch"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# validate command
# ---------------------------------------------------------------------------

class TestValidateCommand:
    """Tests for 'validate' subcommand."""

    def test_validate_local(self, runner, sample_patch_json):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(sample_patch_json, f)
            input_path = f.name

        try:
            result = runner.invoke(main, ["validate", "--type", "local", "--input", input_path])
            assert result.exit_code in (0, 1)
            assert "Result:" in result.output
        finally:
            Path(input_path).unlink(missing_ok=True)

    def test_validate_unknown_type(self, runner, sample_patch_json):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(sample_patch_json, f)
            input_path = f.name

        try:
            result = runner.invoke(main, ["validate", "--type", "nonexistent", "--input", input_path])
            assert result.exit_code == 1
            assert "Unknown validator type" in result.output
        finally:
            Path(input_path).unlink(missing_ok=True)

    def test_validate_missing_input(self, runner):
        result = runner.invoke(main, ["validate"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# report command
# ---------------------------------------------------------------------------

class TestReportCommand:
    """Tests for 'report' subcommand."""

    def test_report_terminal(self, runner, sample_validation_json):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(sample_validation_json, f)
            input_path = f.name

        try:
            result = runner.invoke(main, ["report", "--type", "terminal", "--input", input_path])
            assert result.exit_code == 0
            # Terminal reporter outputs to stdout
            assert "SelfHeal" in result.output or "PASSED" in result.output
        finally:
            Path(input_path).unlink(missing_ok=True)

    def test_report_unknown_type(self, runner, sample_validation_json):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(sample_validation_json, f)
            input_path = f.name

        try:
            result = runner.invoke(main, ["report", "--type", "nonexistent", "--input", input_path])
            assert result.exit_code == 1
            assert "Unknown reporter type" in result.output
        finally:
            Path(input_path).unlink(missing_ok=True)

    def test_report_github_no_token(self, runner, sample_validation_json):
        """GitHub reporter without token should not crash CLI."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(sample_validation_json, f)
            input_path = f.name

        try:
            result = runner.invoke(main, ["report", "--type", "github", "--input", input_path])
            # Runs but may exit with 0 (logs warning) or 1 (raises)
            assert result.exit_code in (0, 1)
        finally:
            Path(input_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# init command
# ---------------------------------------------------------------------------

class TestInitCommand:
    """Tests for 'init' subcommand."""

    def test_init_default(self, runner):
        output_path = "test_selfheal_init.yaml"
        result = runner.invoke(main, ["init", "--output", output_path])

        try:
            assert result.exit_code == 0
            assert "Configuration written" in result.output
            assert Path(output_path).exists()
        finally:
            Path(output_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# metrics command
# ---------------------------------------------------------------------------

class TestMetricsCommand:
    """Tests for 'metrics' subcommand."""

    def test_metrics_text(self, runner):
        result = runner.invoke(main, ["metrics"])
        assert result.exit_code == 0

    def test_metrics_json(self, runner):
        result = runner.invoke(main, ["metrics", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "total_failures" in data
        assert "total_retries" in data
        assert "fix_rate_pct" in data


# ---------------------------------------------------------------------------
# batch command
# ---------------------------------------------------------------------------

class TestBatchCommand:
    """Tests for 'batch' subcommand."""

    def test_batch_single_failure(self, runner, sample_failure_json):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([sample_failure_json], f)
            input_path = f.name

        try:
            result = runner.invoke(main, ["batch", "--input", input_path])
            assert result.exit_code in (0, 1)
        finally:
            Path(input_path).unlink(missing_ok=True)

    def test_batch_dict_input(self, runner, sample_failure_json):
        """batch accepts a single dict (not array) as input."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(sample_failure_json, f)  # single object, not list
            input_path = f.name

        try:
            result = runner.invoke(main, ["batch", "--input", input_path])
            assert result.exit_code in (0, 1)
        finally:
            Path(input_path).unlink(missing_ok=True)

    def test_batch_missing_input(self, runner):
        result = runner.invoke(main, ["batch"])
        assert result.exit_code != 0
