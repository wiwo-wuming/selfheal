"""GitHub reporter implementation."""

import logging
from typing import Optional

from selfheal.config import ReporterConfig, GitHubConfig
from selfheal.events import ValidationEvent
from selfheal.interfaces.reporter import ReporterInterface

logger = logging.getLogger(__name__)


class GitHubReporter(ReporterInterface):
    """Reports results as GitHub Issues."""

    def __init__(self, config: ReporterConfig):
        self.config = config
        self.github_config: Optional[GitHubConfig] = config.github

    name = "github"

    def _get_client(self):
        """Get or create GitHub client."""
        if not self.github_config or not self.github_config.token:
            raise ValueError("GitHub token not configured")

        try:
            from github import Github
            return Github(self.github_config.token)
        except ImportError:
            raise ImportError("PyGithub not installed. Run: pip install selfheal[github]")

    def report(self, event: ValidationEvent) -> None:
        """Report validation event as GitHub Issue."""
        if not self.github_config:
            logger.warning("GitHub config not provided, skipping report")
            return

        try:
            client = self._get_client()
            repo = client.get_repo(f"{self.github_config.owner}/{self.github_config.repo}")

            # Build issue body
            body = self._build_issue_body(event)

            # Create issue
            issue = repo.create_issue(
                title=self._build_title(event),
                body=body,
                labels=self.github_config.labels,
            )

            logger.info(f"Created GitHub Issue #{issue.number}")

        except Exception as e:
            logger.error(f"Failed to create GitHub Issue: {e}")

    def _build_title(self, event: ValidationEvent) -> str:
        """Build issue title."""
        classification = event.patch_event.classification_event
        original = classification.original_event

        return f"[Self-Heal] {classification.category}: {original.error_type}"

    def _build_issue_body(self, event: ValidationEvent) -> str:
        """Build issue body."""
        classification = event.patch_event.classification_event
        original = classification.original_event
        patch = event.patch_event

        body = f"""## Test Failure

**Test Path:** `{original.test_path}`
**Error Type:** {original.error_type}
**Error Message:** {original.error_message}

## Classification

- **Category:** {classification.category}
- **Severity:** {classification.severity.value}
- **Confidence:** {classification.confidence:.0%}

## Generated Patch

```python
{patch.patch_content}
```

## Validation Result

- **Status:** {event.result.upper()}
- **Duration:** {event.duration:.2f}s

---

*This issue was automatically created by SelfHeal.*
"""

        return body
