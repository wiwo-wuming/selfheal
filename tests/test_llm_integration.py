"""Integration tests for LLM classifier and LLM patcher (mock HTTP)."""

import json
from unittest.mock import MagicMock, Mock, patch

import pytest

from selfheal.config import ClassifierConfig, LLMConfig, PatcherConfig
from selfheal.core.classifiers.llm_classifier import LLMClassifier
from selfheal.core.llm_client import LLMClientFactory, LLMResponse
from selfheal.core.patchers.llm_patcher import LLMPatcher
from selfheal.events import ClassificationEvent, ErrorSeverity, TestFailureEvent


# ---------------------------------------------------------------------------
# Reset global cache before each test to avoid cross-test pollution
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_cache():
    """Reset the global LLM response cache before each test."""
    from selfheal.core.cache import reset_cache
    reset_cache()
    LLMClientFactory.reset()
    yield
    reset_cache()
    LLMClientFactory.reset()


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_failure():
    return TestFailureEvent(
        test_path="tests/test_api.py::test_login",
        error_type="AssertionError",
        error_message="Expected 200, got 401",
        traceback="Traceback (most recent call last):\n  File 'test_api.py', line 10\n    assert r.status_code == 200\nAssertionError: Expected 200, got 401",
    )


@pytest.fixture
def sample_classification():
    return ClassificationEvent(
        original_event=TestFailureEvent(
            test_path="tests/test_db.py::test_query",
            error_type="OperationalError",
            error_message="no such table: users",
            traceback="sqlite3.OperationalError: no such table: users",
        ),
        category="runtime",
        severity=ErrorSeverity.HIGH,
        confidence=0.9,
        reasoning="Missing database migration",
    )


# ---------------------------------------------------------------------------
# LLMClassifier tests
# ---------------------------------------------------------------------------

class TestLLMClassifier:
    """Tests for LLM-based classifier."""

    def test_requires_llm_config(self, sample_failure):
        """classify() raises when no LLM config is provided."""
        config = ClassifierConfig(type="llm", llm=None)
        classifier = LLMClassifier(config)
        with pytest.raises(ValueError, match="LLM not configured"):
            classifier.classify(sample_failure)

    def test_unknown_provider_raises(self, sample_failure):
        """LLMClientFactory raises for unknown providers."""
        config = ClassifierConfig(
            type="llm",
            llm=LLMConfig(provider="cohere", model="command", api_key="sk-test"),
        )
        classifier = LLMClassifier(config)
        LLMClientFactory.reset()
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            LLMClientFactory.get_client(classifier.llm_config)

    def test_classify_openai_success(self, sample_failure):
        """classify() returns correct ClassificationEvent on success."""
        mock_response = LLMResponse(
            content=json.dumps({
                "category": "assertion",
                "severity": "high",
                "confidence": 0.95,
                "reasoning": "The error is an assertion failure on HTTP status",
            }),
            tool_result={
                "category": "assertion",
                "severity": "high",
                "confidence": 0.95,
                "reasoning": "The error is an assertion failure on HTTP status",
            },
        )

        config = ClassifierConfig(
            type="llm",
            llm=LLMConfig(provider="openai", model="gpt-4", api_key="sk-test"),
        )
        classifier = LLMClassifier(config)

        with patch("selfheal.core.classifiers.llm_classifier.call_structured", return_value=mock_response) as mock_call:
            result = classifier.classify(sample_failure)

            assert result.category == "assertion"
            assert result.severity == ErrorSeverity.HIGH
            assert result.confidence == 0.95
            assert "assertion failure" in result.reasoning

            # Verify the prompt was sent
            call_args = mock_call.call_args
            assert call_args[1]["tool"]["name"] == "classify_test_failure"
            messages = call_args[1]["messages"]
            assert "Expected 200, got 401" in messages[0]["content"]

    def test_classify_anthropic_success(self, sample_failure):
        """classify() works with Anthropic provider."""
        mock_response = LLMResponse(
            content="",
            tool_result={
                "category": "network",
                "severity": "medium",
                "confidence": 0.8,
                "reasoning": "HTTP 401 suggests auth issue",
            },
        )

        config = ClassifierConfig(
            type="llm",
            llm=LLMConfig(provider="anthropic", model="claude-3", api_key="sk-ant-test"),
        )
        classifier = LLMClassifier(config)

        with patch("selfheal.core.classifiers.llm_classifier.call_structured", return_value=mock_response):
            result = classifier.classify(sample_failure)

            assert result.category == "network"
            assert result.severity == ErrorSeverity.MEDIUM
            assert result.confidence == 0.8

    def test_classify_fallback_on_api_error(self, sample_failure):
        """classify() falls back to 'unknown' on API error."""
        from selfheal.core.llm_client import LLMError

        config = ClassifierConfig(
            type="llm",
            llm=LLMConfig(provider="openai", model="gpt-4", api_key="sk-test"),
        )
        classifier = LLMClassifier(config)

        with patch("selfheal.core.classifiers.llm_classifier.call_structured", side_effect=LLMError("API down")):
            result = classifier.classify(sample_failure)

            assert result.category == "unknown"
            assert result.severity == ErrorSeverity.MEDIUM
            assert result.confidence == 0.0
            assert "LLM error" in result.reasoning

    def test_parse_response_invalid_json(self, sample_failure):
        """_parse_response handles non-JSON responses gracefully."""
        config = ClassifierConfig(
            type="llm",
            llm=LLMConfig(provider="openai", model="gpt-4", api_key="sk-test"),
        )
        classifier = LLMClassifier(config)

        response = LLMResponse(content="No JSON here, just plain text", tool_result=None)
        result = classifier._parse_response(sample_failure, response)

        assert result.category == "unknown"
        assert result.confidence == 0.0
        assert "Failed to parse" in result.reasoning

    def test_parse_response_missing_fields(self, sample_failure):
        """_parse_response uses defaults for missing JSON fields."""
        config = ClassifierConfig(
            type="llm",
            llm=LLMConfig(provider="openai", model="gpt-4", api_key="sk-test"),
        )
        classifier = LLMClassifier(config)

        response = LLMResponse(
            content=json.dumps({"category": "syntax"}),
            tool_result={"category": "syntax"},  # missing severity, confidence, reasoning
        )
        result = classifier._parse_response(sample_failure, response)

        assert result.category == "syntax"
        assert result.severity == ErrorSeverity.MEDIUM  # default
        assert result.confidence == 0.5  # default
        assert result.reasoning == ""

    def test_parse_response_partial_json_extraction(self, sample_failure):
        """_parse_response extracts JSON from text with leading/trailing content."""
        config = ClassifierConfig(
            type="llm",
            llm=LLMConfig(provider="openai", model="gpt-4", api_key="sk-test"),
        )
        classifier = LLMClassifier(config)

        tool_data = {
            "category": "runtime",
            "severity": "critical",
            "confidence": 0.99,
            "reasoning": "DB error",
        }
        response = LLMResponse(content=json.dumps(tool_data), tool_result=tool_data)
        result = classifier._parse_response(sample_failure, response)

        assert result.category == "runtime"
        assert result.severity == ErrorSeverity.CRITICAL
        assert result.confidence == 0.99

    def test_classifier_name(self):
        """Classifier name is 'llm'."""
        config = ClassifierConfig(type="llm")
        classifier = LLMClassifier(config)
        assert classifier.name == "llm"


# ---------------------------------------------------------------------------
# LLMPatcher tests
# ---------------------------------------------------------------------------

class TestLLMPatcher:
    """Tests for LLM-based patch generator."""

    def test_requires_llm_config(self, sample_classification):
        """generate() raises when no LLM config is provided."""
        config = PatcherConfig(type="llm", llm=None)
        patcher = LLMPatcher(config)
        with pytest.raises(ValueError, match="LLM not configured"):
            patcher.generate(sample_classification)

    def test_generate_openai_success(self, sample_classification):
        """generate() returns a PatchEvent with LLM-generated content."""
        patch_content = (
            "The issue is a missing table. Here's the fix:\n\n"
            "```python\n"
            "def setup_db():\n"
            "    conn.execute('CREATE TABLE IF NOT EXISTS users (...)')\n"
            "```"
        )
        mock_response = LLMResponse(content=patch_content, tool_result={"score": 8, "reasoning": "Good fix"})

        config = PatcherConfig(
            type="llm",
            llm=LLMConfig(provider="openai", model="gpt-4", api_key="sk-test"),
        )
        patcher = LLMPatcher(config)

        with patch("selfheal.core.patchers.llm_patcher.call_structured", return_value=mock_response):
            result = patcher.generate(sample_classification)

            assert result.generator.startswith("llm")
            assert "CREATE TABLE" in result.patch_content
            assert result.classification_event == sample_classification
            assert len(result.patch_id) > 0

    def test_generate_fallback_on_api_error(self, sample_classification):
        """generate() returns a PatchEvent with error message on API failure."""
        from selfheal.core.llm_client import LLMError

        config = PatcherConfig(
            type="llm",
            llm=LLMConfig(provider="openai", model="gpt-4", api_key="sk-test"),
        )
        patcher = LLMPatcher(config)

        with patch("selfheal.core.patchers.llm_patcher.call_structured", side_effect=LLMError("API timeout")):
            result = patcher.generate(sample_classification)

            assert result.generator.startswith("llm")
            assert "LLM generation failed" in result.patch_content
            assert result.classification_event == sample_classification

    def test_extract_code_from_blocks(self, sample_classification):
        """_extract_code extracts the largest code block from response."""
        config = PatcherConfig(
            type="llm",
            llm=LLMConfig(provider="openai", model="gpt-4", api_key="sk-test"),
        )
        patcher = LLMPatcher(config)

        response = (
            "Here is a small snippet:\n"
            "```python\nx = 1\n```\n\n"
            "And here is the main fix:\n"
            "```python\n"
            "def fix():\n"
            "    return True\n"
            "```"
        )
        code = patcher._extract_code(response)
        assert "def fix()" in code
        assert "return True" in code

    def test_extract_code_no_blocks(self, sample_classification):
        """_extract_code returns full response when no code blocks found."""
        config = PatcherConfig(
            type="llm",
            llm=LLMConfig(provider="openai", model="gpt-4", api_key="sk-test"),
        )
        patcher = LLMPatcher(config)

        response = "Just some plain text fix"
        code = patcher._extract_code(response)
        assert code == response

    def test_patcher_name(self):
        """Patcher name is 'llm'."""
        config = PatcherConfig(type="llm")
        patcher = LLMPatcher(config)
        assert patcher.name == "llm"

    def test_build_prompt_includes_classification_details(self, sample_classification):
        """_build_prompt includes all classification context."""
        config = PatcherConfig(
            type="llm",
            llm=LLMConfig(provider="openai", model="gpt-4", api_key="sk-test"),
        )
        patcher = LLMPatcher(config)
        prompt = patcher._build_prompt(sample_classification)

        assert "test_db.py::test_query" in prompt
        assert "OperationalError" in prompt
        assert "no such table: users" in prompt
        assert "runtime" in prompt
        assert "high" in prompt
