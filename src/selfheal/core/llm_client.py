"""Unified LLM client factory — singleton per (provider, base_url) pair.

Eliminates duplicated ``_get_client()`` logic in LLMClassifier and LLMPatcher.
Supports structured output via tool use / function calling with automatic
fallback to raw text when the provider doesn't support tool use.

Closely mirrors the credential-resolution and request-creation patterns from
the Anthropic SDK (client.mjs → credentials.mjs → api-promise.mjs).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error classification (inspired by Anthropic SDK core/error.mjs)
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """Base class for all LLM client errors."""

    def __init__(self, message: str, *, provider: str = "", status_code: int = 0):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


class LLMAuthError(LLMError):
    """Invalid or missing API key — do NOT retry."""


class LLMConnectionError(LLMError):
    """Network-level failure — retryable."""


class LLMRateLimitError(LLMError):
    """429 Too Many Requests — retryable with back-off."""


class LLMServerError(LLMError):
    """5xx server error — retryable."""


class LLMTimeoutError(LLMError):
    """Request timed out — retryable."""


# ---------------------------------------------------------------------------
# Unified response wrapper (inspired by core/api-promise.mjs)
# ---------------------------------------------------------------------------

@dataclass
class LLMResponse:
    """Standardized response from any provider.

    Attributes:
        content:     Raw text output from the model.
        tool_result: Parsed structured dict when tool use is active, else None.
        usage:       Token usage dict (input, output, cache_creation, cache_read).
        metadata:    Provider-specific extras (finish_reason, model, latency, …).
    """

    content: str = ""
    tool_result: Optional[dict[str, Any]] = None
    usage: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Retry helpers (inspired by Anthropic SDK's built-in retry logic)
# ---------------------------------------------------------------------------

def _classify_error(exc: Exception, provider: str) -> LLMError:
    """Map provider-specific exceptions to our unified error types."""
    exc_str = str(exc).lower()
    exc_type = type(exc).__name__

    # Anthropic SDK exceptions
    if "authenticationerror" in exc_type or "authentication" in exc_str:
        return LLMAuthError(str(exc), provider=provider)
    if "ratelimiterror" in exc_type or "rate limit" in exc_str or "429" in exc_str:
        return LLMRateLimitError(str(exc), provider=provider, status_code=429)
    if "apiconnectionerror" in exc_type or "connection" in exc_str:
        return LLMConnectionError(str(exc), provider=provider)
    if "timeout" in exc_type or "timeout" in exc_str:
        return LLMTimeoutError(str(exc), provider=provider)
    if "internalservererror" in exc_type or "500" in exc_str or "502" in exc_str or "503" in exc_str:
        return LLMServerError(str(exc), provider=provider, status_code=500)

    # OpenAI SDK exceptions
    if "authenticationerror" in exc_type:
        return LLMAuthError(str(exc), provider=provider)
    if "ratelimiterror" in exc_type:
        return LLMRateLimitError(str(exc), provider=provider, status_code=429)
    if "apiconnectionerror" in exc_type or "apierror" in exc_type:
        return LLMConnectionError(str(exc), provider=provider)
    if "apistatuserror" in exc_type:
        status = getattr(exc, "status_code", 0)
        if status == 429:
            return LLMRateLimitError(str(exc), provider=provider, status_code=429)
        if status >= 500:
            return LLMServerError(str(exc), provider=provider, status_code=status)
        return LLMError(str(exc), provider=provider, status_code=status)

    # Fallback
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return LLMConnectionError(str(exc), provider=provider)
    return LLMError(str(exc), provider=provider)


def call_with_retry(
    fn,
    *,
    max_retries: int = 3,
    backoff_base: float = 2.0,
    provider: str = "",
) -> Any:
    """Execute *fn()* with exponential-backoff retry on transient errors.

    Non-retryable errors (auth, client 4xx except 429) are raised immediately.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as exc:
            classified = _classify_error(exc, provider)
            last_exc = classified

            # Non-retryable
            if isinstance(classified, LLMAuthError):
                raise classified from exc
            if isinstance(classified, LLMError) and classified.status_code and 400 <= classified.status_code < 500 and classified.status_code != 429:
                raise classified from exc

            if attempt < max_retries - 1:
                wait = min(backoff_base ** attempt, 60)
                if isinstance(classified, LLMRateLimitError):
                    wait = max(wait, 2)  # respect rate limits
                logger.warning(
                    "LLM call failed (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, max_retries, wait, classified,
                )
                time.sleep(wait)
            else:
                raise classified from exc
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Client factory (singleton per provider+base_url)
# ---------------------------------------------------------------------------

class LLMClientFactory:
    """Create and cache LLM clients per (provider, base_url).

    Usage::

        client = LLMClientFactory.get_client(llm_config)
    """

    _cache: dict[str, Any] = {}

    @classmethod
    def _key(cls, provider: str, base_url: Optional[str]) -> str:
        return f"{provider.lower()}:{base_url or ''}"

    @classmethod
    def get_client(cls, llm_config: Any) -> Any:
        """Return a cached client instance for the given *llm_config*."""
        key = cls._key(llm_config.provider, llm_config.base_url)
        if key in cls._cache:
            return cls._cache[key]

        provider = llm_config.provider.lower()
        api_key = llm_config.get_api_key()

        if provider in ("openai", "deepseek"):
            try:
                from openai import OpenAI
            except ImportError:
                raise ImportError(
                    "openai package not installed. Run: pip install selfheal[llm]"
                )
            client = OpenAI(api_key=api_key, base_url=llm_config.base_url)

        elif provider == "anthropic":
            try:
                from anthropic import Anthropic
            except ImportError:
                raise ImportError(
                    "anthropic package not installed. Run: pip install selfheal[llm]"
                )
            client = Anthropic(api_key=api_key)

        else:
            raise ValueError(f"Unknown LLM provider: {provider}")

        cls._cache[key] = client
        return client

    @classmethod
    def reset(cls) -> None:
        """Clear the client cache (useful in tests)."""
        cls._cache.clear()


# ---------------------------------------------------------------------------
# Structured output via tool use / function calling
# ---------------------------------------------------------------------------

# Canonical tool schema for classification — used by LLMClassifier
CLASSIFY_TOOL = {
    "name": "classify_test_failure",
    "description": "Classify a test failure into a category with severity and confidence.",
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": [
                    "assertion", "import", "timeout", "network", "syntax",
                    "runtime", "config", "dependency", "resource", "permission",
                    "flaky", "value", "type", "memory", "unknown",
                ],
            },
            "severity": {
                "type": "string",
                "enum": ["critical", "high", "medium", "low"],
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
            },
            "reasoning": {"type": "string"},
        },
        "required": ["category", "severity", "confidence", "reasoning"],
    },
}

# Canonical tool schema for quality scoring — used by LLMPatcher
SCORE_TOOL = {
    "name": "score_patch_quality",
    "description": "Score a code patch on a 0-10 quality scale.",
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 10,
            },
            "reasoning": {"type": "string"},
        },
        "required": ["score", "reasoning"],
    },
}


def call_structured(
    llm_config: Any,
    *,
    system: Optional[list[dict]] = None,
    messages: list[dict],
    tool: dict,
    temperature: float = 0.1,
    max_tokens: int = 1024,
    max_retries: int = 3,
    enable_tool_use: bool = True,
) -> LLMResponse:
    """Call the LLM and return a structured ``LLMResponse``.

    When ``enable_tool_use`` is True and the provider supports it, the tool
    schema is passed to the API so the model returns structured JSON directly.
    Otherwise falls back to raw text + regex extraction.
    """
    client = LLMClientFactory.get_client(llm_config)
    provider = llm_config.provider.lower()

    def _invoke():
        if provider in ("openai", "deepseek"):
            # OpenAI function calling
            openai_messages = []
            if system:
                sys_text = "\n".join(s.get("text", "") for s in system)
                openai_messages.append({"role": "system", "content": sys_text})
            openai_messages.extend(messages)

            kwargs: dict[str, Any] = {
                "model": llm_config.model,
                "messages": openai_messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if enable_tool_use:
                kwargs["tools"] = [{"type": "function", "function": tool}]
                kwargs["tool_choice"] = {
                    "type": "function",
                    "function": {"name": tool["name"]},
                }

            resp = client.chat.completions.create(**kwargs)
            choice = resp.choices[0]

            # Extract structured result from function call
            tool_result = None
            content = choice.message.content or ""
            if enable_tool_use and choice.message.tool_calls:
                import json as _json
                tc = choice.message.tool_calls[0]
                tool_result = _json.loads(tc.function.arguments)
                content = _json.dumps(tool_result)
            elif enable_tool_use and tool_result is None:
                # Fallback: parse JSON from text
                tool_result = _extract_json(content)

            usage = {}
            if resp.usage:
                usage = {
                    "input": resp.usage.prompt_tokens,
                    "output": resp.usage.completion_tokens,
                }
            return LLMResponse(
                content=content,
                tool_result=tool_result,
                usage=usage,
                metadata={"finish_reason": choice.finish_reason, "model": resp.model},
            )

        elif provider == "anthropic":
            anth_messages = list(messages)

            kwargs = {
                "model": llm_config.model,
                "messages": anth_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if system:
                kwargs["system"] = system
            if enable_tool_use:
                kwargs["tools"] = [tool]
                kwargs["tool_choice"] = {"type": "tool", "name": tool["name"]}

            resp = client.messages.create(**kwargs)

            tool_result = None
            content_parts = []
            for block in resp.content:
                if block.type == "text":
                    content_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_result = block.input
            content = "\n".join(content_parts)

            if enable_tool_use and tool_result is None:
                tool_result = _extract_json(content)

            usage = {
                "input": resp.usage.input_tokens,
                "output": resp.usage.output_tokens,
            }
            if hasattr(resp.usage, "cache_creation_input_tokens"):
                usage["cache_creation"] = resp.usage.cache_creation_input_tokens
            if hasattr(resp.usage, "cache_read_input_tokens"):
                usage["cache_read"] = resp.usage.cache_read_input_tokens

            return LLMResponse(
                content=content,
                tool_result=tool_result,
                usage=usage,
                metadata={"stop_reason": resp.stop_reason, "model": resp.model},
            )

        else:
            raise ValueError(f"Unknown provider: {provider}")

    return call_with_retry(
        _invoke,
        max_retries=max_retries,
        provider=provider,
    )


def _extract_json(text: str) -> Optional[dict[str, Any]]:
    """Best-effort JSON extraction from raw text (fallback for non-tool-use)."""
    import json
    import re
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None
