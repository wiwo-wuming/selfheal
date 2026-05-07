"""Webhook reporter for notifications (Slack, Discord, custom) with HMAC signing."""

import hashlib
import hmac
import json
import logging
import time
import urllib.request
import uuid as _uuid
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

    def _compute_signature(self, timestamp: str, nonce: str, payload_bytes: bytes) -> str:
        """Compute HMAC-SHA256 over timestamp, nonce, and payload.

        Returns the hex-encoded signature string, or empty if no secret configured.
        """
        if not self.webhook_secret:
            return ""

        message = f"{timestamp}.{nonce}.".encode("utf-8") + payload_bytes
        mac = hmac.new(
            self.webhook_secret.encode("utf-8"),
            message,
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

        # Anti-replay: timestamp + unique nonce
        timestamp = str(int(time.time()))
        nonce = _uuid.uuid4().hex

        last_error = None
        for attempt in range(_MAX_RETRIES):
            try:
                data = json.dumps(payload).encode("utf-8")
                headers = {
                    "Content-Type": "application/json",
                    "X-SelfHeal-Timestamp": timestamp,
                    "X-SelfHeal-Nonce": nonce,
                }

                # HMAC signature over timestamp + nonce + payload
                signature = self._compute_signature(timestamp, nonce, data)
                if signature:
                    headers["X-SelfHeal-Signature"] = f"sha256={signature}"
                    logger.debug("Webhook signed with HMAC-SHA256 (ts=%s, nonce=%s)", timestamp, nonce[:8])

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

    @staticmethod
    def verify_request(
        secret: str,
        body: bytes,
        signature_header: str,
        timestamp_header: str,
        nonce_header: str,
        max_age_seconds: int = 300,
        seen_nonces: Optional[set] = None,
    ) -> bool:
        """Verify webhook request integrity (anti-replay + anti-tamper).

        Returns True if the request is not replayed, not tampered, and not expired.
        """
        # 1. Timestamp freshness check
        try:
            ts = int(timestamp_header)
        except (ValueError, TypeError):
            return False
        if abs(int(time.time()) - ts) > max_age_seconds:
            return False

        # 2. Nonce uniqueness check
        if seen_nonces is not None:
            if nonce_header in seen_nonces:
                return False
            seen_nonces.add(nonce_header)

        # 3. Signature verification (constant-time compare)
        if not signature_header.startswith("sha256="):
            return False
        expected = signature_header[len("sha256="):]
        message = f"{timestamp_header}.{nonce_header}.".encode("utf-8") + body
        computed = hmac.new(
            secret.encode("utf-8"), message, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(computed, expected)
