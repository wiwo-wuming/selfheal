"""VCR-based integration tests for LLM components (DeepSeek / OpenAI-compatible).

These tests use vcrpy to record and replay real API responses.
They complement the mock-based tests in test_integration_full.py.

Recording mode:
    Set OPENAI_API_KEY (or DEEPSEEK_API_KEY) and run tests.
    Cassettes are saved under tests/vcr_cassettes/.

CI mode:
    CI=1 env var triggers replay-only mode — no API keys needed.
    If a cassette is missing, the test will fail (safe fail).

Usage:
    # Record cassettes (once)
    python -m pytest tests/test_llm_vcr.py -v

    # Replay only (CI)
    CI=1 python -m pytest tests/test_llm_vcr.py -v

    # Rerecord specific cassette
    rm tests/vcr_cassettes/openai/test_llm_classify_assertion.yaml
    python -m pytest tests/test_llm_vcr.py::TestLLMClassifierVCR::test_llm_classify_assertion -v
"""

import os
import pytest

from selfheal.config import ClassifierConfig, PatcherConfig, LLMConfig
from selfheal.events import TestFailureEvent
from selfheal.core.classifiers.llm_classifier import LLMClassifier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_api_key(provider="openai"):
    """Check if the required API key is available."""
    if provider == "openai":
        return bool(os.environ.get("OPENAI_API_KEY"))
    elif provider == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    return False


def _make_failure(error_type="AssertionError", message="assert 1 == 2"):
    return TestFailureEvent(
        test_path="tests/test_calc.py::test_foo",
        error_type=error_type,
        error_message=message,
        traceback="Traceback (most recent call last):\n"
                  "  File \"test_calc.py\", line 5, in test_foo\n"
                  "    assert 1 == 2\n"
                  "AssertionError: assert 1 == 2",
    )


# ---------------------------------------------------------------------------
# OpenAI LLM Classifier VCR tests
# ---------------------------------------------------------------------------

@pytest.mark.vcr
class TestLLMClassifierVCR:
    """VCR-based tests for OpenAI LLM classifier with real API responses."""

    @pytest.fixture
    def openai_classifier(self):
        """Create an LLMClassifier configured for DeepSeek (OpenAI-compatible)."""
        if not _has_api_key("openai") and os.environ.get("CI") != "1":
            pytest.skip("OPENAI_API_KEY not set and not in CI mode")
        cfg = ClassifierConfig(
            type="llm",
            llm=LLMConfig(
                provider="openai",
                model="deepseek-chat",
                api_key="${OPENAI_API_KEY}",
                base_url="https://api.deepseek.com/v1",
                temperature=0,
            ),
        )
        return LLMClassifier(cfg)

    def test_llm_classify_assertion(self, vcr_openai, openai_classifier):
        """Classify a simple assertion error via OpenAI."""
        failure = _make_failure("AssertionError", "assert add(1, 2) == 4")
        with vcr_openai.use_cassette("test_llm_classify_assertion.yaml"):
            result = openai_classifier.classify(failure)
        assert result.category in ("assertion", "unknown")
        assert result.confidence is not None

    def test_llm_classify_import(self, vcr_openai, openai_classifier):
        """Classify an import error via OpenAI."""
        failure = _make_failure(
            "ImportError",
            "No module named 'requests'",
        )
        with vcr_openai.use_cassette("test_llm_classify_import.yaml"):
            result = openai_classifier.classify(failure)
        assert result.category in ("import", "unknown")
        assert result.confidence is not None

    def test_llm_classify_timeout(self, vcr_openai, openai_classifier):
        """Classify a timeout error via OpenAI-compatible API."""
        failure = _make_failure(
            "TimeoutError",
            "Connection timed out after 30s",
        )
        with vcr_openai.use_cassette("test_llm_classify_timeout.yaml"):
            result = openai_classifier.classify(failure)
        # The LLM may classify based on traceback content; accept multiple categories
        assert result.category in ("timeout", "network", "unknown", "assertion")
        assert result.confidence is not None

    def test_llm_classify_large_error(self, vcr_openai, openai_classifier):
        """Classify an error with a long traceback via OpenAI."""
        long_tb = "\n".join(
            [f"  File \"module_{i}.py\", line {i}, in func_{i}" for i in range(20)]
        ) + "\nAssertionError: complex multi-module failure"
        failure = TestFailureEvent(
            test_path="tests/test_complex.py::test_nested",
            error_type="AssertionError",
            error_message="Expected value X but got Y after processing pipeline.",
            traceback=long_tb,
        )
        with vcr_openai.use_cassette("test_llm_classify_large_error.yaml"):
            result = openai_classifier.classify(failure)
        assert result.category is not None
        assert result.confidence is not None


# ---------------------------------------------------------------------------
# Anthropic LLM Classifier VCR tests (conditional)
# ---------------------------------------------------------------------------

# Skip Anthropic tests unless API key is explicitly set (not needed in CI)
_has_anthropic = False
try:
    import anthropic  # noqa: F401
    _has_anthropic = True
except ImportError:
    pass

@pytest.mark.vcr
@pytest.mark.skipif(
    not (_has_api_key("anthropic") or (_has_anthropic and os.environ.get("CI") == "1")),
    reason="ANTHROPIC_API_KEY not set and no cassette available for replay",
)
class TestAnthropicClassifierVCR:
    """VCR-based tests for Anthropic LLM classifier — validates multi-provider consistency."""

    @pytest.fixture
    def anthropic_classifier(self):
        """Create an LLMClassifier configured for Anthropic."""
        cfg = ClassifierConfig(
            type="llm",
            llm=LLMConfig(
                provider="anthropic",
                model="claude-3-haiku-20240307",
                api_key="${ANTHROPIC_API_KEY}",
                temperature=0,
            ),
        )
        return LLMClassifier(cfg)

    def test_anthropic_classify_assertion(self, vcr_anthropic, anthropic_classifier):
        """Classify an assertion error via Anthropic."""
        failure = _make_failure("AssertionError", "assert result == expected")
        with vcr_anthropic.use_cassette("test_anthropic_classify_assertion.yaml"):
            result = anthropic_classifier.classify(failure)
        assert result.category in ("assertion", "value", "unknown")
        assert result.confidence is not None

    def test_anthropic_classify_import(self, vcr_anthropic, anthropic_classifier):
        """Classify an import error via Anthropic."""
        failure = _make_failure("ImportError", "No module named 'requests'")
        with vcr_anthropic.use_cassette("test_anthropic_classify_import.yaml"):
            result = anthropic_classifier.classify(failure)
        assert result.category in ("import", "unknown")
        assert result.confidence is not None

    def test_anthropic_classify_timeout(self, vcr_anthropic, anthropic_classifier):
        """Classify a timeout error via Anthropic."""
        failure = _make_failure("TimeoutError", "Connection timed out after 30s")
        with vcr_anthropic.use_cassette("test_anthropic_classify_timeout.yaml"):
            result = anthropic_classifier.classify(failure)
        assert result.category in ("timeout", "network", "assertion", "unknown")
        assert result.confidence is not None

    def test_anthropic_classify_network(self, vcr_anthropic, anthropic_classifier):
        """Classify a connection error via Anthropic."""
        failure = _make_failure(
            "ConnectionError",
            "Failed to establish connection to api.example.com",
        )
        with vcr_anthropic.use_cassette("test_anthropic_classify_network.yaml"):
            result = anthropic_classifier.classify(failure)
        assert result.category in ("network", "timeout", "unknown")
        assert result.confidence is not None

    def test_anthropic_classify_runtime(self, vcr_anthropic, anthropic_classifier):
        """Classify a runtime error via Anthropic."""
        failure = _make_failure("TypeError", "unsupported operand type(s) for +: 'int' and 'str'")
        with vcr_anthropic.use_cassette("test_anthropic_classify_runtime.yaml"):
            result = anthropic_classifier.classify(failure)
        assert result.category in ("type", "runtime", "value", "unknown")
        assert result.confidence is not None

    def test_anthropic_classify_large_error(self, vcr_anthropic, anthropic_classifier):
        """Classify an error with a long traceback via Anthropic."""
        long_tb = "\n".join(
            [f"  File \"module_{i}.py\", line {i}, in func_{i}" for i in range(20)]
        ) + "\nValueError: invalid input in processing pipeline"
        failure = TestFailureEvent(
            test_path="tests/test_pipeline.py::test_complex",
            error_type="ValueError",
            error_message="Invalid value encountered during processing.",
            traceback=long_tb,
        )
        with vcr_anthropic.use_cassette("test_anthropic_classify_large_error.yaml"):
            result = anthropic_classifier.classify(failure)
        assert result.category is not None
        assert result.confidence is not None


# ---------------------------------------------------------------------------
# Anthropic LLM Patcher VCR tests (conditional)
# ---------------------------------------------------------------------------

@pytest.mark.vcr
@pytest.mark.skipif(
    not (_has_api_key("anthropic") or (_has_anthropic and os.environ.get("CI") == "1")),
    reason="ANTHROPIC_API_KEY not set and no cassette available for replay",
)
class TestAnthropicPatcherVCR:
    """VCR-based tests for Anthropic LLM patcher — validates multi-provider patch generation."""

    @pytest.fixture
    def anthropic_patcher(self):
        """Create an LLMPatcher configured for Anthropic."""
        from selfheal.events import ClassificationEvent, ErrorSeverity
        from selfheal.core.patchers.llm_patcher import LLMPatcher

        cfg = PatcherConfig(
            type="llm",
            llm=LLMConfig(
                provider="anthropic",
                model="claude-3-haiku-20240307",
                api_key="${ANTHROPIC_API_KEY}",
                temperature=0.2,
            ),
            refine_rounds=1,  # VCR cassette only has single round recorded
        )
        self._cls = ClassificationEvent(
            original_event=_make_failure("AssertionError", "assert add(1, 2) == 4"),
            category="assertion",
            severity=ErrorSeverity.MEDIUM,
            confidence=0.85,
        )
        return LLMPatcher(cfg)

    def test_anthropic_patch_assertion_error(self, vcr_anthropic, anthropic_patcher):
        """Generate a patch for an assertion error via Anthropic."""
        with vcr_anthropic.use_cassette("test_anthropic_patch_assertion_error.yaml"):
            result = anthropic_patcher.generate(self._cls)
        assert result.patch_content is not None
        assert len(result.patch_content) > 0
        assert result.generator == "llm"

    def test_anthropic_patch_import_error(self, vcr_anthropic, anthropic_patcher):
        """Generate a patch for an import error via Anthropic."""
        from selfheal.events import ClassificationEvent, ErrorSeverity
        cls = ClassificationEvent(
            original_event=_make_failure("ImportError", "No module named 'requests'"),
            category="import",
            severity=ErrorSeverity.HIGH,
            confidence=0.9,
        )
        with vcr_anthropic.use_cassette("test_anthropic_patch_import_error.yaml"):
            result = anthropic_patcher.generate(cls)
        assert result.patch_content is not None
        assert len(result.patch_content) > 0

    def test_anthropic_patch_no_markdown_fences(self, vcr_anthropic, anthropic_patcher):
        """Verify the Anthropic patcher strips markdown fences from output."""
        with vcr_anthropic.use_cassette("test_anthropic_patch_no_markdown_fences.yaml"):
            result = anthropic_patcher.generate(self._cls)
        assert result.patch_content is not None
        # Patch content should not contain markdown fences
        assert "```" not in result.patch_content.strip()


# ---------------------------------------------------------------------------
# OpenAI LLM Patcher VCR tests
# ---------------------------------------------------------------------------

@pytest.mark.vcr
class TestLLMPatcherVCR:
    """VCR-based tests for OpenAI LLM patcher with real API responses."""

    @pytest.fixture
    def openai_patcher(self):
        """Create an LLMPatcher configured for DeepSeek (OpenAI-compatible)."""
        if not _has_api_key("openai") and os.environ.get("CI") != "1":
            pytest.skip("OPENAI_API_KEY not set and not in CI mode")
        from selfheal.events import ClassificationEvent, ErrorSeverity
        from selfheal.core.patchers.llm_patcher import LLMPatcher
        cfg = PatcherConfig(
            type="llm",
            llm=LLMConfig(
                provider="openai",
                model="deepseek-chat",
                api_key="${OPENAI_API_KEY}",
                base_url="https://api.deepseek.com/v1",
                temperature=0.2,
            ),
            refine_rounds=1,  # VCR cassette only has single round recorded
        )
        # Create a sample classification for the patcher context
        self._cls = ClassificationEvent(
            original_event=_make_failure(),
            category="assertion",
            severity=ErrorSeverity.MEDIUM,
            confidence=0.85,
        )
        return LLMPatcher(cfg)

    def test_llm_patch_import_error(self, vcr_openai, openai_patcher):
        """Generate a patch for an import error via OpenAI."""
        with vcr_openai.use_cassette("test_llm_patch_import_error.yaml"):
            result = openai_patcher.generate(self._cls)
        assert result.patch_content is not None
        assert len(result.patch_content) > 0
        assert result.generator == "llm"

    def test_llm_patch_returns_code_block(self, vcr_openai, openai_patcher):
        """Verify the LLM patcher extracts code from the response."""
        with vcr_openai.use_cassette("test_llm_patch_returns_code_block.yaml"):
            result = openai_patcher.generate(self._cls)
        assert result.patch_content is not None
        # Patch content should not contain markdown fences
        assert "```" not in result.patch_content.strip()
