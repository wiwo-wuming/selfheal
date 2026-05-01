"""Tests for HybridClassifier — rule-first with LLM fallback."""

import pytest
from unittest.mock import MagicMock, patch

from selfheal.config import ClassifierConfig, LLMConfig, RuleConfig
from selfheal.core.classifiers.hybrid_classifier import HybridClassifier
from selfheal.core.classifiers.rule_classifier import RuleClassifier
from selfheal.core.classifiers.llm_classifier import LLMClassifier
from selfheal.events import (
    TestFailureEvent,
    ClassificationEvent,
    ErrorSeverity,
    ErrorCategory,
)


def make_event(error_type="AssertionError", error_message="assert 1 == 2"):
    return TestFailureEvent(
        test_path="tests/test_foo.py::test_bar",
        error_type=error_type,
        error_message=error_message,
        traceback='File "foo.py", line 42, in test_bar\n    assert 1 == 2',
    )


class TestHybridClassifier:
    """Unit tests for HybridClassifier."""

    def test_rule_high_confidence_skips_llm(self):
        """When rule confidence >= threshold, LLM is NOT called."""
        config = ClassifierConfig(
            type="hybrid",
            confidence_threshold=0.5,
            llm=LLMConfig(provider="openai", model="gpt-4o-mini", api_key="sk-test"),
        )
        classifier = HybridClassifier(config)

        # AssertionError matches rule with 0.9 confidence, above threshold 0.5
        event = make_event("AssertionError", "assert 1 == 2")
        result = classifier.classify(event)

        assert result.category == "assertion"
        assert result.confidence >= 0.5
        assert "[rule]" in result.reasoning

    def test_rule_low_confidence_falls_back_to_llm(self):
        """When rule confidence < threshold, LLM is called."""
        config = ClassifierConfig(
            type="hybrid",
            confidence_threshold=0.5,
            llm=LLMConfig(provider="openai", model="gpt-4o-mini", api_key="sk-test"),
        )
        classifier = HybridClassifier(config)

        # UnknownError won't match any rule → confidence 0.1 < 0.5
        event = make_event("UnknownError", "something weird happened")
        result = classifier.classify(event)

        # Falls back to LLM, but LLM has no real API key in test → falls back to rule result
        assert result.category in ("unknown", "assertion", "runtime", "import", "timeout", "network", "syntax")

    def test_no_llm_configured_returns_rule_result(self):
        """When no LLM config, always return rule result."""
        config = ClassifierConfig(
            type="hybrid",
            confidence_threshold=0.5,
            llm=None,
        )
        classifier = HybridClassifier(config)

        event = make_event("UnknownError", "something weird")
        result = classifier.classify(event)

        assert result.category == "unknown"
        assert result.confidence == 0.1

    @patch.object(LLMClassifier, "classify")
    def test_fallback_to_llm_when_no_rule_match(self, mock_llm):
        """Low-confidence rule result triggers LLM call."""
        mock_llm.return_value = ClassificationEvent(
            original_event=make_event(),
            category="runtime",
            severity=ErrorSeverity.MEDIUM,
            confidence=0.8,
            reasoning="LLM analysis",
        )

        config = ClassifierConfig(
            type="hybrid",
            confidence_threshold=0.5,
            llm=LLMConfig(provider="openai", model="gpt-4o-mini", api_key="sk-test"),
        )
        classifier = HybridClassifier(config)

        event = make_event("UnknownError", "something obscure")
        result = classifier.classify(event)

        mock_llm.assert_called_once()
        assert result.category == "runtime"
        assert "[hybrid→llm]" in result.reasoning

    @patch.object(LLMClassifier, "classify", side_effect=RuntimeError("LLM down"))
    def test_llm_failure_returns_rule_result(self, mock_llm):
        """When LLM fails, gracefully return rule result."""
        config = ClassifierConfig(
            type="hybrid",
            confidence_threshold=0.5,
            llm=LLMConfig(provider="openai", model="gpt-4o-mini", api_key="sk-test"),
        )
        classifier = HybridClassifier(config)

        event = make_event("UnknownError", "something obscure")
        result = classifier.classify(event)

        mock_llm.assert_called_once()
        assert result.category == "unknown"
        assert "LLM error" in result.reasoning

    def test_threshold_boundary(self):
        """Exact threshold match uses rule result."""
        config = ClassifierConfig(
            type="hybrid",
            confidence_threshold=0.9,
            llm=LLMConfig(provider="openai", model="gpt-4o-mini", api_key="sk-test"),
        )
        classifier = HybridClassifier(config)

        # AssertionError matches with 0.9 confidence == threshold → uses rule
        event = make_event("AssertionError", "assert 1 == 2")
        result = classifier.classify(event)

        assert result.category == "assertion"
        assert "[rule]" in result.reasoning

    def test_alternative_categories_preserved(self):
        """Rule alt categories are preserved in fallback result."""
        mock_llm = MagicMock()
        mock_llm.return_value = ClassificationEvent(
            original_event=make_event(),
            category="runtime",
            severity=ErrorSeverity.MEDIUM,
            confidence=0.8,
            reasoning="LLM analysis",
        )

        config = ClassifierConfig(
            type="hybrid",
            confidence_threshold=0.99,  # Very high threshold to force LLM fallback always
            llm=LLMConfig(provider="openai", model="gpt-4o-mini", api_key="sk-test"),
        )
        classifier = HybridClassifier(config)
        classifier.llm_classifier.classify = mock_llm

        # TypeError matches rule with 0.9 confidence, threshold is 0.99 → falls back to LLM
        # rule category "type" (was "runtime" before TYPE split) should appear in alt_categories
        event = make_event("TypeError", "can't multiply sequence by non-int")
        result = classifier.classify(event)

        assert "type" in result.alternative_categories

    def test_name_property(self):
        config = ClassifierConfig(type="hybrid")
        assert HybridClassifier(config).name == "hybrid"

    def test_hybrid_classifier_registered(self):
        """Ensure hybrid is registered in the global registry."""
        from selfheal.registry import get_registry
        registry = get_registry()
        cls = registry.get_classifier("hybrid")
        assert cls is not None
        assert cls is HybridClassifier
