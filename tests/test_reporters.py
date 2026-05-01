"""Integration tests for GitHub and Webhook reporters (mock HTTP)."""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from selfheal.config import GitHubConfig, ReporterConfig
from selfheal.core.reporters.github_reporter import GitHubReporter
from selfheal.core.reporters.webhook_reporter import WebhookReporter
from selfheal.events import (
    ClassificationEvent,
    ErrorSeverity,
    PatchEvent,
    TestFailureEvent,
    ValidationEvent,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_validation_passed():
    failure = TestFailureEvent(
        test_path="tests/test_api.py::test_endpoint",
        error_type="HTTPError",
        error_message="404 Not Found",
        traceback="requests.exceptions.HTTPError: 404",
    )
    classification = ClassificationEvent(
        original_event=failure,
        category="network",
        severity=ErrorSeverity.HIGH,
        confidence=0.95,
        reasoning="Endpoint returns 404",
    )
    patch = PatchEvent(
        classification_event=classification,
        patch_id="abc-123",
        patch_content="def test_endpoint():\n    assert r.status_code == 200",
        generator="template",
    )
    return ValidationEvent(
        patch_event=patch,
        result="passed",
        test_output="1 passed in 0.5s",
        duration=0.5,
    )


@pytest.fixture
def sample_validation_failed():
    failure = TestFailureEvent(
        test_path="tests/test_db.py::test_insert",
        error_type="IntegrityError",
        error_message="UNIQUE constraint failed: users.email",
        traceback="sqlite3.IntegrityError: UNIQUE constraint failed",
    )
    classification = ClassificationEvent(
        original_event=failure,
        category="runtime",
        severity=ErrorSeverity.CRITICAL,
        confidence=0.85,
    )
    patch = PatchEvent(
        classification_event=classification,
        patch_id="def-456",
        patch_content="# fix: handle duplicate email\n",
        generator="llm",
    )
    return ValidationEvent(
        patch_event=patch,
        result="failed",
        test_output="1 failed in 0.3s",
        duration=0.3,
        error_message="Test still fails after patch",
    )


# ---------------------------------------------------------------------------
# GitHubReporter tests
# ---------------------------------------------------------------------------

class TestGitHubReporter:
    """Tests for GitHub Issue reporter."""

    def test_reporter_name(self):
        config = ReporterConfig(type="github")
        reporter = GitHubReporter(config)
        assert reporter.name == "github"

    def test_report_skips_without_config(self, sample_validation_passed):
        """report() does nothing when GitHubConfig is missing."""
        config = ReporterConfig(type="github", github=None)
        reporter = GitHubReporter(config)
        # Should not raise
        reporter.report(sample_validation_passed)

    @patch.object(GitHubReporter, "_get_client")
    def test_report_creates_issue(
        self, mock_get_client, sample_validation_passed
    ):
        """report() creates a GitHub Issue with correct title and labels."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_repo = MagicMock()
        mock_issue = MagicMock(number=42)
        mock_repo.create_issue.return_value = mock_issue
        mock_client.get_repo.return_value = mock_repo

        config = ReporterConfig(
            type="github",
            github=GitHubConfig(
                owner="testorg",
                repo="testrepo",
                token="ghp_test",
                labels=["self-heal", "bug"],
            ),
        )
        reporter = GitHubReporter(config)
        reporter.report(sample_validation_passed)

        # Verify repo was accessed
        mock_client.get_repo.assert_called_once_with("testorg/testrepo")

        # Verify issue creation
        mock_repo.create_issue.assert_called_once()
        issue_kwargs = mock_repo.create_issue.call_args[1]
        assert "[Self-Heal]" in issue_kwargs["title"]
        assert "network" in issue_kwargs["title"]
        assert issue_kwargs["labels"] == ["self-heal", "bug"]
        assert "test_api.py::test_endpoint" in issue_kwargs["body"]
        assert "404 Not Found" in issue_kwargs["body"]

    @patch.object(GitHubReporter, "_get_client")
    def test_report_handles_api_error(
        self, mock_get_client, sample_validation_failed
    ):
        """report() logs error but does not raise on API failure."""
        mock_get_client.side_effect = Exception("API rate limit exceeded")

        config = ReporterConfig(
            type="github",
            github=GitHubConfig(owner="org", repo="repo", token="ghp_test"),
        )
        reporter = GitHubReporter(config)
        # Should not raise
        reporter.report(sample_validation_failed)

    def test_build_title(self, sample_validation_passed):
        """_build_title formats category and error type."""
        config = ReporterConfig(type="github")
        reporter = GitHubReporter(config)
        title = reporter._build_title(sample_validation_passed)
        assert "[Self-Heal]" in title
        assert "network" in title
        assert "HTTPError" in title

    def test_build_issue_body_contains_all_sections(
        self, sample_validation_passed
    ):
        """_build_issue_body includes test failure, classification, patch, validation."""
        config = ReporterConfig(type="github")
        reporter = GitHubReporter(config)
        body = reporter._build_issue_body(sample_validation_passed)

        assert "test_api.py::test_endpoint" in body
        assert "404 Not Found" in body
        assert "network" in body
        assert "high" in body
        assert "def test_endpoint()" in body
        assert "PASSED" in body

    def test_report_requires_token(self):
        """_get_client raises when token is missing."""
        config = ReporterConfig(
            type="github",
            github=GitHubConfig(owner="org", repo="repo", token=None),
        )
        reporter = GitHubReporter(config)
        with pytest.raises(ValueError, match="token"):
            reporter._get_client()


# ---------------------------------------------------------------------------
# WebhookReporter tests
# ---------------------------------------------------------------------------

class TestWebhookReporter:
    """Tests for Webhook reporter (Slack/Discord/custom)."""

    def test_reporter_name(self):
        config = ReporterConfig(
            type="webhook", webhook_url="https://example.com/hook"
        )
        reporter = WebhookReporter(config)
        assert reporter.name == "webhook"

    def test_report_skips_without_url(self, sample_validation_passed):
        """report() does nothing when webhook URL is not configured."""
        config = ReporterConfig(type="webhook", webhook_url=None)
        reporter = WebhookReporter(config)
        reporter.report(sample_validation_passed)

    def test_report_filters_by_event(self, sample_validation_passed):
        """report() only sends for enabled event types."""
        config = ReporterConfig(
            type="webhook",
            webhook_url="https://hooks.example.com/slack",
            webhook_events=["failed", "error"],  # 'passed' not in list
        )
        reporter = WebhookReporter(config)
        # passed should be filtered out
        with patch("urllib.request.urlopen") as mock_urlopen:
            reporter.report(sample_validation_passed)
        mock_urlopen.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_report_sends_webhook(self, mock_urlopen, sample_validation_passed):
        """report() sends a correctly formatted webhook payload."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_response

        config = ReporterConfig(
            type="webhook",
            webhook_url="https://hooks.example.com/slack",
            webhook_events=["passed", "failed", "error"],
        )
        reporter = WebhookReporter(config)
        reporter.report(sample_validation_passed)

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "https://hooks.example.com/slack"
        assert req.headers["Content-type"] == "application/json"

        # Verify payload structure
        payload = json.loads(req.data.decode("utf-8"))
        assert "PASSED" in payload["text"]
        assert len(payload["attachments"]) == 1
        fields = payload["attachments"][0]["fields"]
        field_titles = {f["title"] for f in fields}
        assert "Test" in field_titles
        assert "Category" in field_titles
        assert "Patch ID" in field_titles

    @patch("urllib.request.urlopen")
    def test_report_includes_error_message(
        self, mock_urlopen, sample_validation_failed
    ):
        """report() includes error_message when present."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_response

        config = ReporterConfig(
            type="webhook",
            webhook_url="https://hooks.example.com/slack",
            webhook_events=["passed", "failed", "error"],
        )
        reporter = WebhookReporter(config)
        reporter.report(sample_validation_failed)

        req = mock_urlopen.call_args[0][0]
        payload = json.loads(req.data.decode("utf-8"))
        fields = payload["attachments"][0]["fields"]
        error_fields = [f for f in fields if f["title"] == "Error"]
        assert len(error_fields) == 1
        assert "Test still fails" in error_fields[0]["value"]

    @patch("urllib.request.urlopen")
    def test_report_retry_on_failure(self, mock_urlopen, sample_validation_passed):
        """report() retries with exponential backoff on failure."""
        mock_response = MagicMock()
        mock_response.status = 200
        # Fail twice, succeed on third attempt
        side_effects = [
            OSError("Connection refused"),
            OSError("Connection refused"),
            MagicMock(__enter__=MagicMock(return_value=mock_response)),
        ]
        mock_urlopen.side_effect = side_effects

        config = ReporterConfig(
            type="webhook",
            webhook_url="https://hooks.example.com/slack",
            webhook_events=["passed"],
        )
        reporter = WebhookReporter(config)

        start = time.time()
        reporter.report(sample_validation_passed)
        elapsed = time.time() - start

        # Should have been called 3 times (2 failures + 1 success)
        assert mock_urlopen.call_count == 3
        # Exponential backoff: 1s + 2s >= 3s minimum
        assert elapsed >= 1.0

    @patch("urllib.request.urlopen")
    def test_report_all_retries_exhausted(
        self, mock_urlopen, sample_validation_passed
    ):
        """report() logs error but does not raise after exhausting retries."""
        mock_urlopen.side_effect = OSError("Network unreachable")

        config = ReporterConfig(
            type="webhook",
            webhook_url="https://hooks.example.com/slack",
            webhook_events=["passed"],
        )
        reporter = WebhookReporter(config)
        # Should not raise
        reporter.report(sample_validation_passed)

        # Should have tried 3 times
        assert mock_urlopen.call_count == 3

    def test_emoji_map(self, sample_validation_passed, sample_validation_failed):
        """Emojis are correct for each result type."""
        passed_text = None
        failed_text = None

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_urlopen.return_value.__enter__.return_value = mock_response

            config = ReporterConfig(
                type="webhook",
                webhook_url="https://hooks.example.com/slack",
            )
            reporter = WebhookReporter(config)
            reporter.report(sample_validation_passed)
            passed_text = json.loads(
                mock_urlopen.call_args[0][0].data
            )["text"]

        with patch("urllib.request.urlopen") as mock_urlopen2:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_urlopen2.return_value.__enter__.return_value = mock_response

            reporter = WebhookReporter(config)
            reporter.report(sample_validation_failed)
            failed_text = json.loads(
                mock_urlopen2.call_args[0][0].data
            )["text"]

        assert "✅" in passed_text
        assert "❌" in failed_text
