"""LLM-based patcher implementation."""

import logging
import uuid
from typing import Any, Optional

from selfheal.config import PatcherConfig, LLMConfig
from selfheal.events import ClassificationEvent, PatchEvent
from selfheal.interfaces.patcher import PatcherInterface

logger = logging.getLogger(__name__)


class LLMPatcher(PatcherInterface):
    """LLM-based intelligent patch generator."""

    def __init__(self, config: PatcherConfig):
        self.config = config
        self.llm_config: Optional[LLMConfig] = config.llm
        self._client: Optional[Any] = None

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
                raise ImportError("openai package not installed")
        elif provider == "anthropic":
            try:
                from anthropic import Anthropic
                self._client = Anthropic(api_key=self.llm_config.get_api_key())
            except ImportError:
                raise ImportError("anthropic package not installed")
        else:
            raise ValueError(f"Unknown LLM provider: {provider}")

        return self._client

    def generate(self, classification: ClassificationEvent) -> PatchEvent:
        """Generate a patch using LLM."""
        if not self.llm_config:
            raise ValueError("LLM not configured")

        prompt = self._build_prompt(classification)

        try:
            response = self._call_llm(prompt)
            content = self._extract_code(response)

            return PatchEvent(
                classification_event=classification,
                patch_id=str(uuid.uuid4()),
                patch_content=content,
                generator="llm",
            )
        except (ConnectionError, TimeoutError, ValueError, RuntimeError, OSError) as e:
            logger.error(f"LLM patch generation failed: {e}")
            return PatchEvent(
                classification_event=classification,
                patch_id=str(uuid.uuid4()),
                patch_content=f"# LLM generation failed: {str(e)}\n",
                generator="llm",
            )

    def _build_prompt(self, classification: ClassificationEvent) -> str:
        """Build patch generation prompt."""
        event = classification.original_event

        return f"""Generate a fix for the following test failure.

Test: {event.test_path}
Error Type: {event.error_type}
Error Message: {event.error_message}
Category: {classification.category}
Severity: {classification.severity.value}

Traceback:
{event.traceback}

Generate a patch that fixes this issue. Respond with:
1. Brief explanation of the fix
2. The complete code patch

Code should be well-formatted and follow best practices."""

    def _call_llm(self, prompt: str) -> str:
        """Call LLM API."""
        client = self._get_client()
        provider = self.llm_config.provider.lower()

        if provider == "openai":
            response = client.chat.completions.create(
                model=self.llm_config.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            return response.choices[0].message.content
        elif provider == "anthropic":
            response = client.messages.create(
                model=self.llm_config.model,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text

    def _extract_code(self, response: str) -> str:
        """Extract code from LLM response."""
        import re

        # Try to extract code blocks
        code_blocks = re.findall(r"```(?:\w+)?\n(.*?)```", response, re.DOTALL)

        if code_blocks:
            # Return the largest code block
            return max(code_blocks, key=len).strip()

        # If no code blocks, return full response with explanation
        return response.strip()
