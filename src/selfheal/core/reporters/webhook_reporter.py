"""Webhook reporter for notifications (Slack, Discord, custom) with HMAC signing."""

import hashlib
import hmac
import json
import logging
import time
import urllib.request
from datetime import datetime
from typing import Optional

from selfheal.config import ReporterConfig
from selfheal.events import ValidationEvent
from selfheal.interfaces.reporter import ReporterInterface

logger = logging.getLogger(__name__)

# Retry configuration for webhook delivery
_MAX_RETRIES = 3
_RETRY_DELAY_BASE = 1.0  # seconds, exponential backoff


class WebhookReporter(ReporterInterface):
    """Sends notifications via webhook with optional HMAC-SHA256 signing."""

    name = "webhook"

    def __init__(self, config: ReporterConfig):
        self.config = config
        self.webhook_url = config.webhook_url
        self.webhook_secret: Optional[str] = self._resolve_secret(config)
        self.enabled_events = set(config.webhook_events)

    @staticmethod
    def _resolve_secret(config: ReporterConfig) -> Optional[str]:
        """Resolve webhook secret from config, supporting ${ENV} placeholders."""
        from selfheal.config import _resolve_env

        secret = getattr(config, "webhook_secret", None)
        if secret and isinstance(secret, str) and "${" in secret:
            return _resolve_env(secret)
        if secret and isinstance(secret, str):
            return secret.strip()
        return None

    def _compute_signature(self, payload_bytes: bytes) -> str:
        """Compute HMAC-SHA256 signature for the payload.

        Returns the hex-encoded signature string, or empty if no secret configured.
        """
        if not self.webhook_secret:
            return ""

        mac = hmac.new(
            self.webhook_secret.encode("utf-8"),
            payload_bytes,
            hashlib.sha256,
        )
        return mac.hexdigest()

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
                headers = {"Content-Type": "application/json"}

                # Add HMAC signature if secret is configured
                signature = self._compute_signature(data)
                if signature:
                    headers["X-SelfHeal-Signature"] = f"sha256={signature}"
                    logger.debug("Webhook request signed with HMAC-SHA256")

                req = urllib.request.Request(
                    self.webhook_url,
                    data=data,
                    headers=headers,
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    logger.info(f"Webhook sent, status: {resp.status}")
                    return  # success, no need to retry
            except (KeyboardInterrupt, SystemExit):
                raise
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
