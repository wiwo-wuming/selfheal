"""Performance benchmarks for SelfHeal core pipeline components.

Usage:
    pytest tests/test_benchmark.py --benchmark-only
    pytest tests/test_benchmark.py --benchmark-min-rounds=10
"""

from selfheal.config import (
    ClassifierConfig,
    Config,
    EngineConfig,
    PatcherConfig,
    RuleConfig,
    StoreConfig,
    ValidatorConfig,
)
from selfheal.core.classifiers.hybrid_classifier import HybridClassifier
from selfheal.core.classifiers.rule_classifier import RuleClassifier
from selfheal.core.patchers.template_patcher import TemplatePatcher
from selfheal.engine import SelfHealEngine
from selfheal.events import (
    ClassificationEvent,
    ErrorSeverity,
    TestFailureEvent,
)


# ── Test fixtures ──────────────────────────────────────────────────────────

def _make_event(error_type: str, error_message: str = "") -> TestFailureEvent:
    return TestFailureEvent(
        test_path="tests/test_example.py::test_stuff",
        error_type=error_type,
        error_message=error_message or f"Example {error_type} error",
        traceback=f"Traceback (most recent call last):\n  File \"test_example.py\", line 10, in test_stuff\n    raise {error_type}()\n",
    )


# ── Rule classifier benchmarks ─────────────────────────────────────────────

def test_bench_rule_classifier_assertion(benchmark):
    """Benchmark rule classifier on a common AssertionError."""
    classifier = RuleClassifier(ClassifierConfig(type="rule"))
    event = _make_event("AssertionError", "assert 1 == 2")
    result = benchmark(classifier.classify, event)
    assert result.category == "assertion"


def test_bench_rule_classifier_import(benchmark):
    """Benchmark rule classifier on an ImportError."""
    classifier = RuleClassifier(ClassifierConfig(type="rule"))
    event = _make_event("ImportError", "No module named 'numpy'")
    result = benchmark(classifier.classify, event)
    assert result.category == "import"


def test_bench_rule_classifier_timeout(benchmark):
    """Benchmark rule classifier on a TimeoutError."""
    classifier = RuleClassifier(ClassifierConfig(type="rule"))
    event = _make_event("TimeoutError", "Test timed out after 300 seconds")
    result = benchmark(classifier.classify, event)
    assert result.category == "timeout"


def test_bench_rule_classifier_network(benchmark):
    """Benchmark rule classifier on a network error."""
    classifier = RuleClassifier(ClassifierConfig(type="rule"))
    event = _make_event(
        "requests.exceptions.ConnectionError",
        "Connection refused",
    )
    result = benchmark(classifier.classify, event)
    assert result.category == "network"


def test_bench_rule_classifier_unknown(benchmark):
    """Benchmark rule classifier on an unrecognized error (worst-case path)."""
    classifier = RuleClassifier(ClassifierConfig(type="rule"))
    event = _make_event(
        "WeirdCustomError",
        "Something completely unexpected happened here",
    )
    result = benchmark(classifier.classify, event)
    assert result.category == "unknown"


def test_bench_rule_classifier_warm_cache(benchmark):
    """Benchmark rule classifier with repeated calls (cache-warm path)."""
    classifier = RuleClassifier(ClassifierConfig(type="rule"))
    event = _make_event("AssertionError", "assert False")
    # Run once to warm cache, then benchmark subsequent calls
    classifier.classify(event)

    def _run():
        return classifier.classify(event)

    result = benchmark(_run)
    assert result.category == "assertion"


def test_bench_rule_classifier_custom_rule(benchmark):
    """Benchmark with a custom regex rule added to config."""
    config = ClassifierConfig(
        type="rule",
        rules=[
            RuleConfig(
                pattern=r"custom_retry_error",
                category="flaky",
                severity="low",
            )
        ],
    )
    classifier = RuleClassifier(config)
    event = _make_event(
        "RuntimeError",
        "custom_retry_error: connection lost, retrying",
    )
    result = benchmark(classifier.classify, event)
    assert result.category == "flaky"


# ── Hybrid classifier benchmarks ───────────────────────────────────────────

def test_bench_hybrid_classifier_rule_path(benchmark):
    """Benchmark hybrid classifier when rule confidence is high (no LLM call)."""
    classifier = HybridClassifier(ClassifierConfig(type="hybrid"))
    event = _make_event("AssertionError", "assert x == y")
    result = benchmark(classifier.classify, event)
    assert result.category == "assertion"


def test_bench_hybrid_classifier_fallback_path_no_llm(benchmark):
    """Benchmark hybrid classifier fallback when no LLM is configured."""
    classifier = HybridClassifier(ClassifierConfig(type="hybrid"))
    event = _make_event(
        "CustomBizarreError",
        "Something weird with no known pattern",
    )
    result = benchmark(classifier.classify, event)
    # Falls through to rule result (unknown) since no LLM configured
    assert result.category == "unknown"


# ── Template patcher benchmarks ────────────────────────────────────────────

def test_bench_template_patcher_assertion(benchmark):
    """Benchmark template patcher generating a patch for assertion errors."""
    patcher = TemplatePatcher(PatcherConfig(type="template"))
    classification = ClassificationEvent(
        original_event=_make_event("AssertionError"),
        category="assertion",
        severity=ErrorSeverity.MEDIUM,
        confidence=0.9,
        reasoning="[rule] assert match",
    )
    result = benchmark(patcher.generate, classification)
    assert result.patch_id
    # Assertion template may fall back to descriptive-assert or use template
    assert result.patch_content


def test_bench_template_patcher_import_error(benchmark):
    """Benchmark template patcher for import errors."""
    patcher = TemplatePatcher(PatcherConfig(type="template"))
    classification = ClassificationEvent(
        original_event=_make_event("ImportError"),
        category="import",
        severity=ErrorSeverity.HIGH,
        confidence=0.95,
        reasoning="[rule] import match",
    )
    result = benchmark(patcher.generate, classification)
    assert result.patch_id


def test_bench_template_patcher_network(benchmark):
    """Benchmark template patcher for network errors."""
    patcher = TemplatePatcher(PatcherConfig(type="template"))
    classification = ClassificationEvent(
        original_event=_make_event("ConnectionError"),
        category="network",
        severity=ErrorSeverity.HIGH,
        confidence=0.95,
        reasoning="[rule] network match",
    )
    result = benchmark(patcher.generate, classification)
    assert result.patch_id


def test_bench_template_patcher_fallback(benchmark):
    """Benchmark template patcher fallback (worst-case, no template match)."""
    patcher = TemplatePatcher(PatcherConfig(type="template"))
    classification = ClassificationEvent(
        original_event=_make_event("BizarreError"),
        category="unknown",
        severity=ErrorSeverity.MEDIUM,
        confidence=0.1,
        reasoning="[rule] unknown",
    )
    result = benchmark(patcher.generate, classification)
    assert result.patch_id


# ── Pipeline end-to-end benchmarks (minimal) ───────────────────────────────

def _make_minimal_engine() -> SelfHealEngine:
    """Create a minimal engine for benchmarking (no LLM, no docker, etc.)."""
    config = Config(
        classifier=ClassifierConfig(type="rule"),
        patcher=PatcherConfig(type="template"),
        validator=ValidatorConfig(type="local", timeout=30),
        store=StoreConfig(type="memory"),
        engine=EngineConfig(max_retries=1, auto_apply=False, async_batch=False),
    )
    return SelfHealEngine(config)


def test_bench_pipeline_minimal_e2e(benchmark):
    """Benchmark a full pipeline run on a simple assertion error."""
    engine = _make_minimal_engine()
    event = _make_event("AssertionError", "assert 1 == 2")

    def _run():
        return engine.process_failure(event)

    result = benchmark(_run)
    assert result.result in ("passed", "failed", "error")


def test_bench_pipeline_import_error_e2e(benchmark):
    """Benchmark a full pipeline run on an import error."""
    engine = _make_minimal_engine()
    event = _make_event(
        "ModuleNotFoundError",
        "No module named 'nonexistent_package'",
    )

    def _run():
        return engine.process_failure(event)

    result = benchmark(_run)
    assert result.result in ("passed", "failed", "error")


# ── Batch processing benchmarks ────────────────────────────────────────────

def test_bench_batch_sequential_10_events(benchmark):
    """Benchmark sequential batch processing of 10 failures."""
    engine = _make_minimal_engine()
    events = [
        _make_event("AssertionError", f"assert x == {i}")
        for i in range(10)
    ]

    def _run():
        return engine.process_batch(events)

    results = benchmark(_run)
    assert len(results) == 10


def test_bench_batch_sequential_50_events(benchmark):
    """Benchmark sequential batch processing of 50 failures."""
    engine = _make_minimal_engine()
    events = [
        _make_event("AssertionError", f"assert x == {i}")
        for i in range(50)
    ]

    # Use fewer rounds for large batches
    def _run():
        return engine.process_batch(events)

    results = benchmark(_run)
    assert len(results) == 50


def test_bench_batch_async_10_events(benchmark):
    """Benchmark async batch processing of 10 failures with concurrency=4."""
    config = Config(
        classifier=ClassifierConfig(type="rule"),
        patcher=PatcherConfig(type="template"),
        validator=ValidatorConfig(type="local", timeout=30),
        store=StoreConfig(type="memory"),
        engine=EngineConfig(
            max_retries=1,
            auto_apply=False,
            async_batch=True,
            max_concurrency=4,
        ),
    )
    engine = SelfHealEngine(config)
    events = [
        _make_event("AssertionError", f"assert x == {i}")
        for i in range(10)
    ]

    def _run():
        return engine.process_batch(events)

    results = benchmark(_run)
    assert len(results) == 10


# ── Misc component benchmarks ──────────────────────────────────────────────

def test_bench_event_creation(benchmark):
    """Benchmark raw event creation speed."""
    def _run():
        return TestFailureEvent(
            test_path="tests/test_x.py::test_y",
            error_type="AssertionError",
            error_message="assert 1 == 2",
            traceback="Traceback...\n  raise AssertionError()\n",
        )

    result = benchmark(_run)
    assert result.test_path == "tests/test_x.py::test_y"


def test_bench_config_load(benchmark):
    """Benchmark default config instantiation speed."""
    def _run():
        return Config()

    result = benchmark(_run)
    assert result is not None
