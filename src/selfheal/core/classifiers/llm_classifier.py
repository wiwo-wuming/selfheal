"""LLM-based classifier implementation."""

import logging
from typing import Optional

from selfheal.config import ClassifierConfig, LLMConfig
from selfheal.events import TestFailureEvent, ClassificationEvent, ErrorSeverity
from selfheal.interfaces.classifier import ClassifierInterface
from selfheal.core.llm_client import (
    call_structured,
    CLASSIFY_TOOL,
    LLMResponse,
    LLMError,
)

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
        # Cache integration
        self._cache_enabled = getattr(config, "cache_enabled", True)
        self._cache_ttl = getattr(config, "cache_ttl", 3600.0)

    name = "llm"

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
        try:
            response = self._call_llm(event)
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
        except LLMError as e:
            logger.error(f"LLM classification failed: {e}")
            return ClassificationEvent(
                original_event=event,
                category="unknown",
                severity=ErrorSeverity.MEDIUM,
                confidence=0.0,
                reasoning=f"LLM error: {str(e)}",
            )

    def _build_system(self) -> list[dict]:
        """Build system prompt with classification instructions and cache control."""
        categories = ", ".join([
            "assertion", "import", "timeout", "network", "syntax",
            "runtime", "config", "dependency", "resource", "permission",
            "flaky", "value", "type", "memory", "unknown",
        ])
        severities = ", ".join([s.value for s in ErrorSeverity])

        system_text = (
            "You are a test failure classifier. "
            "Analyze the error and classify it into the most appropriate category.\n\n"
            f"Categories: {categories}\n"
            f"Severities: {severities}"
        )

        blocks: list[dict] = [{"type": "text", "text": system_text}]
        if getattr(self.llm_config, "enable_prompt_caching", False):
            blocks[0]["cache_control"] = {"type": "ephemeral"}
        return blocks

    def _build_messages(self, event: TestFailureEvent) -> list[dict]:
        """Build user messages containing only the event information."""
        user_text = (
            f"Error Type: {event.error_type}\n"
            f"Error Message: {event.error_message}\n\n"
            f"Traceback:\n{event.traceback[:500]}"
        )
        return [{"role": "user", "content": user_text}]

    def _call_llm(self, event: TestFailureEvent) -> LLMResponse:
        """Call LLM with structured tool use and automatic retry."""
        assert self.llm_config is not None

        return call_structured(
            self.llm_config,
            system=self._build_system(),
            messages=self._build_messages(event),
            tool=CLASSIFY_TOOL,
            temperature=self.llm_config.temperature,
            max_tokens=1024,
            max_retries=self.llm_config.max_retries,
            enable_tool_use=self.llm_config.enable_tool_use,
        )

    def _parse_response(
        self,
        event: TestFailureEvent,
        response: LLMResponse,
    ) -> ClassificationEvent:
        """Parse structured LLM response into a ClassificationEvent."""
        data = response.tool_result
        if not data:
            return ClassificationEvent(
                original_event=event,
                category="unknown",
                severity=ErrorSeverity.MEDIUM,
                confidence=0.0,
                reasoning="Failed to parse LLM response",
            )

        return ClassificationEvent(
            original_event=event,
            category=data.get("category", "unknown"),
            severity=ErrorSeverity(data.get("severity", "medium")),
            confidence=float(data.get("confidence", 0.5)),
            reasoning=data.get("reasoning", ""),
        )
