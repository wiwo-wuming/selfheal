"""LLM-based classifier implementation."""

import logging
from typing import Any, Optional

from selfheal.config import ClassifierConfig, LLMConfig
from selfheal.events import TestFailureEvent, ClassificationEvent, ErrorSeverity
from selfheal.interfaces.classifier import ClassifierInterface

logger = logging.getLogger(__name__)


class LLMClassifier(ClassifierInterface):
    """LLM-based error classifier for intelligent categorization.

    Uses an optional response cache (configured via ``cache_enabled`` in
    ``ClassifierConfig``) to avoid repeated API calls for identical errors.
    """

    def __init__(self, config: ClassifierConfig):
        self.config = config
        self.llm_config: Optional[LLMConfig] = None
        if config.llm:
            self.llm_config = config.llm
        self._client: Optional[Any] = None
        # Cache integration
        self._cache_enabled = getattr(config, "cache_enabled", True)
        self._cache_ttl = getattr(config, "cache_ttl", 3600.0)

    name = "llm"

    def _get_client(self):
        """Get or create LLM client."""
        if self._client:
            return self._client

        if not self.llm_config:
            raise ValueError("LLM configuration not provided")

        provider = self.llm_config.provider.lower()

        if provider in ("openai", "deepseek"):
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    api_key=self.llm_config.get_api_key(),
                    base_url=self.llm_config.base_url,
                )
            except ImportError:
                raise ImportError("openai package not installed. Run: pip install selfheal[llm]")
        elif provider == "anthropic":
            try:
                from anthropic import Anthropic
                self._client = Anthropic(api_key=self.llm_config.get_api_key())
            except ImportError:
                raise ImportError("anthropic package not installed. Run: pip install selfheal[llm]")
        else:
            raise ValueError(f"Unknown LLM provider: {provider}")

        return self._client

    def classify(self, event: TestFailureEvent) -> ClassificationEvent:
        """Classify a test failure using LLM, with optional cache."""
        if not self.llm_config:
            raise ValueError("LLM not configured")

        # --- cache lookup ---
        cached = None
        if self._cache_enabled:
            from selfheal.core.cache import get_cache
            cache = get_cache(ttl=self._cache_ttl)
            cache_key = cache.make_key(event)
            cached = cache.get(cache_key)
            if cached:
                logger.info("LLM cache hit for %s", event.error_type)
                return ClassificationEvent(
                    original_event=event,
                    category=cached["category"],
                    severity=ErrorSeverity(cached["severity"]),
                    confidence=float(cached["confidence"]),
                    reasoning=f"[cached] {cached.get('reasoning', '')}",
                )

        # --- actual LLM call ---
        prompt = self._build_prompt(event)

        try:
            response = self._call_llm(prompt)
            result = self._parse_response(event, response)

            # --- cache write ---
            if self._cache_enabled and cached is None and result.confidence > 0:
                from selfheal.core.cache import get_cache
                cache = get_cache(ttl=self._cache_ttl)
                cache_key = cache.make_key(event)
                cache.set(cache_key, {
                    "category": result.category,
                    "severity": result.severity.value,
                    "confidence": result.confidence,
                    "reasoning": result.reasoning,
                })
                logger.debug("LLM cache stored for %s", cache_key)

            return result
        except (ConnectionError, TimeoutError, ValueError, RuntimeError, OSError) as e:
            logger.error(f"LLM classification failed: {e}")
            return ClassificationEvent(
                original_event=event,
                category="unknown",
                severity=ErrorSeverity.MEDIUM,
                confidence=0.0,
                reasoning=f"LLM error: {str(e)}",
            )

    def _build_prompt(self, event: TestFailureEvent) -> str:
        """Build classification prompt."""
        categories = ", ".join([
            "assertion", "import", "timeout", "network", "syntax",
            "runtime", "config", "dependency", "resource", "permission",
            "flaky", "value", "type", "memory", "unknown",
        ])
        severities = ", ".join([s.value for s in ErrorSeverity])

        return f"""Classify the following test failure.

Error Type: {event.error_type}
Error Message: {event.error_message}

Traceback:
{event.traceback[:500]}

Categories: {categories}
Severities: {severities}

Respond with JSON:
{{
    "category": "<most likely category>",
    "severity": "<severity level>",
    "confidence": <0.0-1.0>,
    "reasoning": "<brief explanation>"
}}"""

    def _call_llm(self, prompt: str) -> str:
        """Call LLM API."""
        client = self._get_client()
        provider = self.llm_config.provider.lower()

        if provider == "openai":
            response = client.chat.completions.create(
                model=self.llm_config.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            return response.choices[0].message.content
        elif provider == "anthropic":
            response = client.messages.create(
                model=self.llm_config.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text

    def _parse_response(
        self,
        event: TestFailureEvent,
        response: str
    ) -> ClassificationEvent:
        """Parse LLM response."""
        import json
        import re

        # Extract JSON from response
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if not json_match:
            return ClassificationEvent(
                original_event=event,
                category="unknown",
                severity=ErrorSeverity.MEDIUM,
                confidence=0.0,
                reasoning="Failed to parse LLM response",
            )

        try:
            data = json.loads(json_match.group())
            return ClassificationEvent(
                original_event=event,
                category=data.get("category", "unknown"),
                severity=ErrorSeverity(data.get("severity", "medium")),
                confidence=float(data.get("confidence", 0.5)),
                reasoning=data.get("reasoning", ""),
            )
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse LLM JSON: {e}")
            return ClassificationEvent(
                original_event=event,
                category="unknown",
                severity=ErrorSeverity.MEDIUM,
                confidence=0.0,
                reasoning=f"Parse error: {str(e)}",
            )
