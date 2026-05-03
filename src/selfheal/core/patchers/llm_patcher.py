"""LLM-based patcher with multi-round self-refinement."""

import logging
import uuid
from typing import Any, Optional

from selfheal.config import PatcherConfig, LLMConfig
from selfheal.events import ClassificationEvent, PatchEvent
from selfheal.interfaces.patcher import PatcherInterface

logger = logging.getLogger(__name__)

# Multi-round refinement: generate -> self-review -> refine
_DEFAULT_REFINE_ROUNDS = 2         # total rounds (generate + refine)
_MAX_REFINE_ROUNDS = 5             # safety cap


class LLMPatcher(PatcherInterface):
    """LLM-based intelligent patch generator with multi-round self-refinement.

    When ``refine_rounds`` is set (default 2), the patcher performs a
    generate → self-review → refine loop.  The LLM first generates a
    patch, then re-reads its own output as a reviewer looking for bugs,
    and finally produces an improved patch.

    This feedback mechanism is entirely prompt-based — no test execution
    is needed.  The pipeline-level ``PatchStage`` still handles the
    traditional retry-on-apply-failure loop.
    """

    def __init__(self, config: PatcherConfig):
        self.config = config
        self.llm_config: Optional[LLMConfig] = config.llm
        self._client: Optional[Any] = None
        self.refine_rounds = min(
            max(getattr(config, "refine_rounds", _DEFAULT_REFINE_ROUNDS), 1),
            _MAX_REFINE_ROUNDS,
        )

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
        """Generate a patch with optionally multiple refinement rounds.

        Round 1: generate initial patch.
        Round 2..N: self-review → refine using feedback.
        """
        if not self.llm_config:
            raise ValueError("LLM not configured")

        try:
            # --- Round 1: initial generation ---
            prompt = self._build_prompt(classification)
            response = self._call_llm(prompt)
            content = self._extract_code(response)

            if self.refine_rounds <= 1:
                return PatchEvent(
                    classification_event=classification,
                    patch_id=str(uuid.uuid4()),
                    patch_content=content,
                    generator="llm",
                )

            # --- Rounds 2..N: self-review + refine ---
            for round_num in range(2, self.refine_rounds + 1):
                logger.info(f"LLM self-refine round {round_num}/{self.refine_rounds}")
                review_prompt = self._build_review_prompt(classification, content)
                review_response = self._call_llm(review_prompt)
                refined = self._extract_code(review_response)
                if refined and refined != content:
                    content = refined
                else:
                    logger.debug("Refine round produced no change, keeping previous patch")

            return PatchEvent(
                classification_event=classification,
                patch_id=str(uuid.uuid4()),
                patch_content=content,
                generator=f"llm(refined_{self.refine_rounds}r)",
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
        """Build the initial patch generation prompt."""
        event = classification.original_event

        return f"""You are a senior Python developer fixing a broken test.

Test path: {event.test_path}
Error type: {event.error_type}
Error message: {event.error_message}
Category: {classification.category}
Severity: {classification.severity.value}

Traceback:
{event.traceback}

Analyze the traceback to find the **root cause in the source code** (not the test file).
Then generate a unified-diff patch that fixes the source code.

Rules:
- Output ONLY the unified diff patch inside ```diff ... ``` fences.
- The patch MUST change the source code under test, NOT the test file itself.
- Do NOT use pytest.skip / pytest.xfail / try-except to hide the error.
- If an import is missing, add it. If a function signature is wrong, fix it.
- If a value is incorrect, correct it based on the error message.
- Include a brief explanation BEFORE the diff."""

    def _build_review_prompt(
        self, classification: ClassificationEvent, current_patch: str
    ) -> str:
        """Build a self-review prompt that asks the LLM to critique its own patch."""
        event = classification.original_event

        return f"""You are a senior code reviewer. Review the following patch that was
generated to fix a test failure, then produce an IMPROVED version.

=== ORIGINAL ERROR ===
Test: {event.test_path}
Error type: {event.error_type}
Error message: {event.error_message}
Category: {classification.category}
Traceback:
{event.traceback[:800]}

=== CURRENT PATCH (to review) ===
{current_patch}

=== REVIEW TASK ===
1. Does this patch actually fix the root cause? If not, what is missing?
2. Could it break anything else?
3. Is there a simpler or more correct fix?

Then produce the IMPROVED unified-diff patch inside ```diff ... ``` fences.
Output ONLY the diff patch, no extra commentary."""

    def _call_llm(self, prompt: str) -> str:
        """Call LLM API."""
        assert self.llm_config is not None
        client = self._get_client()
        provider = self.llm_config.provider.lower()

        if provider in ("openai", "deepseek"):
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
        else:
            raise ValueError(f"Unknown LLM provider: {provider}")

    @staticmethod
    def _extract_code(response: str) -> str:
        """Extract code from LLM response."""
        import re

        # Try diff fence first
        diff_blocks = re.findall(r"```diff\n(.*?)```", response, re.DOTALL)
        if diff_blocks:
            return max(diff_blocks, key=len).strip()

        # Then any code fence
        code_blocks = re.findall(r"```(?:\w+)?\n(.*?)```", response, re.DOTALL)
        if code_blocks:
            return max(code_blocks, key=len).strip()

        # Fallback: return full response stripped
        return response.strip()
