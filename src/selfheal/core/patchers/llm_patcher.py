"""LLM-based patcher with multi-round self-refinement."""

import logging
import uuid
from typing import Any, Generator, Optional

from selfheal.config import PatcherConfig, LLMConfig
from selfheal.core.llm_client import (
    LLMClientFactory,
    LLMError,
    LLMResponse,
    SCORE_TOOL,
    call_structured,
)
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

    The generate + review steps are done in a **single multi-turn
    conversation** so the LLM retains full context.  Quality scoring
    is a separate call that uses tool-use for structured output.

    This feedback mechanism is entirely prompt-based — no test execution
    is needed.  The pipeline-level ``PatchStage`` still handles the
    traditional retry-on-apply-failure loop.
    """

    def __init__(self, config: PatcherConfig):
        self.config = config
        self.llm_config: Optional[LLMConfig] = config.llm
        self.refine_rounds = min(
            max(getattr(config, "refine_rounds", _DEFAULT_REFINE_ROUNDS), 1),
            _MAX_REFINE_ROUNDS,
        )

    name = "llm"

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    def _build_system(self) -> list[dict[str, Any]]:
        """Build the system prompt with optional cache control."""
        system_text = (
            "You are a senior Python developer and code reviewer specializing in "
            "fixing broken tests.\n"
            "When generating patches, output ONLY unified-diff format inside "
            "```diff fences.\n"
            "When reviewing patches, focus on root cause correctness and minimal "
            "changes."
        )
        system: list[dict[str, Any]] = [{"type": "text", "text": system_text}]
        if self.llm_config and self.llm_config.enable_prompt_caching:
            system[0]["cache_control"] = {"type": "ephemeral"}
        return system

    # ------------------------------------------------------------------
    # Main entry points
    # ------------------------------------------------------------------

    def generate(self, classification: ClassificationEvent) -> PatchEvent:
        """Generate a patch with multi-turn self-refinement and quality scoring.

        Call 1 — multi-turn conversation (generate + review + refine):
            The LLM generates an initial patch, then (if refine_rounds > 1)
            we append its reply and a review prompt so it refines the patch
            in context.

        Call 2 — quality scoring via tool use:
            The LLM scores the final patch on a 0-10 scale using the
            ``SCORE_TOOL`` schema, returning structured ``{score, reasoning}``.
        """
        if not self.llm_config:
            raise ValueError("LLM not configured")

        quality_threshold = getattr(self, "quality_threshold", None)
        if quality_threshold is None:
            quality_threshold = float(
                getattr(self.config, "quality_threshold", 4.0)
            )

        try:
            # --- Call 1: multi-turn generate + review + refine ---
            system = self._build_system()
            generate_prompt = self._build_prompt(classification)
            messages: list[dict[str, str]] = [
                {"role": "user", "content": generate_prompt},
            ]

            response: LLMResponse = call_structured(
                self.llm_config,
                system=system,
                messages=messages,
                tool=SCORE_TOOL,  # placeholder; tool use disabled
                temperature=self.llm_config.temperature,
                max_tokens=4096,
                max_retries=self.llm_config.max_retries,
                enable_tool_use=False,
            )
            content = self._extract_code(response.content)
            patch_suffix = ""

            # --- Rounds 2..N: self-review + refine (same conversation) ---
            if self.refine_rounds > 1:
                for round_num in range(2, self.refine_rounds + 1):
                    logger.info(
                        "LLM self-refine round %d/%d",
                        round_num,
                        self.refine_rounds,
                    )
                    review_prompt = self._build_review_prompt(
                        classification, content
                    )
                    # Append assistant reply + user review to conversation
                    messages.append(
                        {"role": "assistant", "content": response.content}
                    )
                    messages.append(
                        {"role": "user", "content": review_prompt}
                    )

                    response = call_structured(
                        self.llm_config,
                        system=system,
                        messages=messages,
                        tool=SCORE_TOOL,  # placeholder; tool use disabled
                        temperature=self.llm_config.temperature,
                        max_tokens=4096,
                        max_retries=self.llm_config.max_retries,
                        enable_tool_use=False,
                    )
                    refined = self._extract_code(response.content)
                    if refined and refined != content:
                        content = refined
                    else:
                        logger.debug(
                            "Refine round produced no change, "
                            "keeping previous patch"
                        )
                patch_suffix = f"(refined_{self.refine_rounds}r)"

            # --- Call 2: quality scoring via tool use ---
            score = self._score_patch(classification, content)
            patch_suffix += f" score={score}/10"
            logger.info("LLM quality score: %d/10 for patch", score)

            if score < quality_threshold:
                logger.warning(
                    "Patch quality score %d/10 below threshold %d, rejecting",
                    score,
                    quality_threshold,
                )
                return PatchEvent(
                    classification_event=classification,
                    patch_id=str(uuid.uuid4()),
                    patch_content=(
                        "# SelfHeal: LLM patch rejected (quality score "
                        f"{score}/10 < threshold {quality_threshold})\n"
                        "# Original patch:\n" + content
                    ),
                    generator=(
                        "llm"
                        + "".join(patch_suffix.split(" score")[0:1])
                        if " score" in patch_suffix
                        else ""
                    ) + "[rejected]",
                )

            return PatchEvent(
                classification_event=classification,
                patch_id=str(uuid.uuid4()),
                patch_content=content,
                generator=f"llm{patch_suffix}",
            )
        except (LLMError, ConnectionError, TimeoutError, ValueError, RuntimeError, OSError) as e:
            logger.error("LLM patch generation failed: %s", e)
            return PatchEvent(
                classification_event=classification,
                patch_id=str(uuid.uuid4()),
                patch_content=f"# LLM generation failed: {str(e)}\n",
                generator="llm",
            )

    def generate_stream(
        self, classification: ClassificationEvent
    ) -> Generator[str, None, None]:
        """Stream patch generation, yielding text chunks as they arrive.

        This is a single-pass generation (no refinement rounds) that
        streams the LLM response for real-time display.
        """
        if not self.llm_config:
            raise ValueError("LLM not configured")

        client = LLMClientFactory.get_client(self.llm_config)
        provider = self.llm_config.provider.lower()
        system = self._build_system()

        if provider == "anthropic":
            messages = self._build_messages(classification)
            with client.messages.stream(
                model=self.llm_config.model,
                system=system,
                messages=messages,
                max_tokens=4096,
            ) as stream:
                for text_delta in stream.text_deltas:
                    yield text_delta

        elif provider in ("openai", "deepseek"):
            openai_messages: list[dict[str, Any]] = []
            sys_text = "\n".join(s.get("text", "") for s in system)
            openai_messages.append({"role": "system", "content": sys_text})
            openai_messages.extend(self._build_messages(classification))

            resp = client.chat.completions.create(
                model=self.llm_config.model,
                messages=openai_messages,
                stream=True,
            )
            for chunk in resp:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        else:
            raise ValueError(f"Unknown LLM provider: {provider}")

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    def _build_messages(
        self, classification: ClassificationEvent
    ) -> list[dict[str, str]]:
        """Build user messages for Anthropic-style API."""
        return [{"role": "user", "content": self._build_prompt(classification)}]

    def _build_messages_openai(
        self, classification: ClassificationEvent
    ) -> list[dict[str, str]]:
        """Build user messages for OpenAI-style API."""
        return [{"role": "user", "content": self._build_prompt(classification)}]

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

    # ------------------------------------------------------------------
    # Quality scoring (tool use)
    # ------------------------------------------------------------------

    def _score_patch(
        self, classification: ClassificationEvent, patch_content: str
    ) -> int:
        """Score a patch on a 0-10 scale via LLM self-evaluation using tool use.

        Uses ``SCORE_TOOL`` so the model returns structured ``{score, reasoning}``
        instead of raw text that requires regex parsing.

        0-3:  harmful (silent pass, xfail, skip, empty try-except)
        4-6:  acceptable (defensive guard with logging)
        7-8:  good (addresses root cause)
        9-10: excellent (elegant, minimal, correct)
        """
        event = classification.original_event

        score_prompt = f"""You are a senior code reviewer. Score the following patch on a 0-10 scale.

**Scoring guide:**
- 0-3: Harmful — uses pytest.skip, pytest.xfail, empty try/except/pass to hide the error
- 4-6: Acceptable — adds defensive guard with proper error logging
- 7-8: Good — addresses the root cause correctly
- 9-10: Excellent — elegant, minimal change, fully correct

=== TEST FAILURE ===
Test: {event.test_path}
Error type: {event.error_type}
Error message: {event.error_message[:300]}
Category: {classification.category}

=== PATCH ===
{patch_content[:800]}"""

        try:
            score_response: LLMResponse = call_structured(
                self.llm_config,
                system=self._build_system(),
                messages=[{"role": "user", "content": score_prompt}],
                tool=SCORE_TOOL,
                temperature=0.1,
                max_tokens=512,
                max_retries=self.llm_config.max_retries,
                enable_tool_use=self.llm_config.enable_tool_use,
            )

            if score_response.tool_result and "score" in score_response.tool_result:
                score = int(score_response.tool_result["score"])
                return max(0, min(10, score))

            # Fallback: try to parse from raw text (tool use disabled or failed)
            import re
            match = re.search(r"\b(\d+)\b", score_response.content.strip())
            if match:
                score = int(match.group(1))
                return max(0, min(10, score))

            logger.warning(
                "Could not parse quality score from response: %s",
                score_response.content[:80],
            )
        except LLMError:
            logger.debug("Quality scoring skipped due to LLM error", exc_info=True)
        except Exception:
            logger.debug("Quality scoring skipped", exc_info=True)

        # Fallback heuristic: detect bad patterns
        bad = ["pytest.skip", "pytest.xfail", "pass  #", "pass\n"]
        good = ["def ", "import ", "return ", "class "]
        bad_count = sum(1 for p in bad if p in patch_content)
        good_count = sum(1 for p in good if p in patch_content)
        if bad_count > good_count:
            return 3
        return 6

    # ------------------------------------------------------------------
    # Code extraction (unchanged)
    # ------------------------------------------------------------------

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
