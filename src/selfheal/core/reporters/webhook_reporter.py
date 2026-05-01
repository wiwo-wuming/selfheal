"""Webhook reporter for notifications (Slack, Discord, custom)."""

import json
import logging
import time
import urllib.request
from datetime import datetime

from selfheal.config import ReporterConfig
from selfheal.events import ValidationEvent
from selfheal.interfaces.reporter import ReporterInterface

logger = logging.getLogger(__name__)

# Retry configuration for webhook delivery
_MAX_RETRIES = 3
_RETRY_DELAY_BASE = 1.0  # seconds, exponential backoff


class WebhookReporter(ReporterInterface):
    """Sends notifications via webhook."""

    name = "webhook"

    def __init__(self, config: ReporterConfig):
        self.config = config
        self.webhook_url = config.webhook_url
        self.enabled_events = set(config.webhook_events)

    def report(self, event: ValidationEvent) -> None:
        """Send a webhook notification."""
        if not self.webhook_url:
            logger.warning("Webhook URL not configured, skipping notification")
            return

        if event.result not in self.enabled_events:
            return

        classification = event.patch_event.classification_event
        original = classification.original_event
        patch = event.patch_event

        emoji_map = {"passed": "✅", "failed": "❌", "error": "⚠️"}
        emoji = emoji_map.get(event.result, "ℹ️")

        payload = {
            "text": f"{emoji} SelfHeal: Test {event.result.upper()}",
            "attachments": [
                {
                    "color": {"passed": "good", "failed": "danger", "error": "warning"}.get(event.result, "gray"),
                    "fields": [
                        {"title": "Test", "value": original.test_path, "short": True},
                        {"title": "Category", "value": classification.category, "short": True},
                        {"title": "Severity", "value": classification.severity.value, "short": True},
                        {"title": "Confidence", "value": f"{classification.confidence:.0%}", "short": True},
                        {"title": "Result", "value": event.result, "short": True},
                        {"title": "Duration", "value": f"{event.duration:.2f}s", "short": True},
                        {"title": "Generator", "value": patch.generator, "short": True},
                        {"title": "Patch ID", "value": patch.patch_id, "short": True},
                    ],
                    "footer": f"SelfHeal | {datetime.now().isoformat()}",
                }
            ],
        }

        if event.error_message:
            payload["attachments"][0]["fields"].append(
                {"title": "Error", "value": event.error_message[:500], "short": False}
            )

        last_error = None
        for attempt in range(_MAX_RETRIES):
            try:
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    self.webhook_url,
                    data=data,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    logger.info(f"Webhook sent, status: {resp.status}")
                    return  # success, no need to retry
            except Exception as e:
                last_error = e
                if attempt < _MAX_RETRIES - 1:
                    wait = _RETRY_DELAY_BASE * (2 ** attempt)
                    logger.warning(
                        f"Webhook attempt {attempt + 1} failed, "
                        f"retrying in {wait:.0f}s: {e}"
                    )
                    time.sleep(wait)
                else:
                    logger.error(
                        f"Webhook notification failed after "
                        f"{_MAX_RETRIES} attempts: {last_error}"
                    )
