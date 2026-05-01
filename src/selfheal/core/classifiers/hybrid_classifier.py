"""Hybrid classifier: rule-first with LLM fallback.

Pipeline: RuleClassifier → (confidence < threshold?) → LLMClassifier
This saves API costs by only invoking the LLM when rules are uncertain.
"""

import logging
from typing import Optional

from selfheal.config import ClassifierConfig, LLMConfig
from selfheal.core.classifiers.rule_classifier import RuleClassifier
from selfheal.core.classifiers.llm_classifier import LLMClassifier
from selfheal.events import TestFailureEvent, ClassificationEvent, ErrorSeverity, ErrorCategory
from selfheal.interfaces.classifier import ClassifierInterface

logger = logging.getLogger(__name__)

# Default confidence threshold for rule-first fallback
DEFAULT_HYBRID_THRESHOLD = 0.5


class HybridClassifier(ClassifierInterface):
    """Rule-first classifier that falls back to LLM when rules are uncertain.

    Flow:
    1. Run RuleClassifier — if confidence >= *confidence_threshold*, return immediately.
    2. Otherwise (no match or low confidence), invoke LLMClassifier.
    3. If LLM also fails, return the best-effort rule result.

    Configuration (in ClassifierConfig or YAML):
        classifier:
          type: hybrid
          confidence_threshold: 0.6   # default 0.5
          rules: [...]                 # rule list (passed to RuleClassifier)
          llm:                         # LLM config (passed to LLMClassifier)
            provider: openai
            model: gpt-4o-mini
    """

    name = "hybrid"

    def __init__(self, config: ClassifierConfig):
        self.config = config
        self.rule_classifier = RuleClassifier(config)
        self.llm_classifier: Optional[LLMClassifier] = None
        if config.llm:
            self.llm_classifier = LLMClassifier(config)

        threshold = getattr(config, "confidence_threshold", None)
        if threshold is None:
            threshold = DEFAULT_HYBRID_THRESHOLD
        self.confidence_threshold = float(threshold)

        logger.info(
            "HybridClassifier ready: rule threshold=%.2f, llm=%s",
            self.confidence_threshold,
            "enabled" if self.llm_classifier else "disabled",
        )

    def classify(self, event: TestFailureEvent) -> ClassificationEvent:
        """Classify with rules first, fall back to LLM if needed."""

        # Step 1: Try rule-based classification
        rule_result = self.rule_classifier.classify(event)

        # If high confidence and not unknown, return immediately
        if (
            rule_result.confidence >= self.confidence_threshold
            and rule_result.category != ErrorCategory.UNKNOWN.value
        ):
            logger.debug(
                "Hybrid: rule match (category=%s, confidence=%.2f) → skip LLM",
                rule_result.category, rule_result.confidence,
            )
            rule_result.reasoning = f"[rule] {rule_result.reasoning}"
            return rule_result

        # Step 2: Fall back to LLM
        if self.llm_classifier is None:
            logger.debug(
                "Hybrid: no LLM configured, returning rule result (confidence=%.2f)",
                rule_result.confidence,
            )
            return rule_result

        logger.info(
            "Hybrid: rule confidence too low (%.2f < %.2f), falling back to LLM",
            rule_result.confidence, self.confidence_threshold,
        )

        try:
            llm_result = self.llm_classifier.classify(event)
            llm_result.reasoning = f"[hybrid→llm] {llm_result.reasoning}"
            # Preserve rule alt categories if rule had a match
            if rule_result.category != ErrorCategory.UNKNOWN.value:
                llm_result.alternative_categories = [
                    rule_result.category,
                    *llm_result.alternative_categories,
                ]
            return llm_result
        except Exception as exc:
            logger.warning("Hybrid: LLM fallback failed (%s), returning rule result", exc)
            rule_result.reasoning = f"[rule-fallback] {rule_result.reasoning} (LLM error: {exc})"
            return rule_result
