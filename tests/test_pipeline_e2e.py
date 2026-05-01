"""E2E integration tests for the full SelfHeal pipeline.

Covers classify→patch→validate→report→store , auto-apply, error resilience,
custom pipeline config, multi-reporter, store persistence, metrics, plugin gate.
"""

import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from selfheal.config import (
    Config, EngineConfig, PipelineConfig, PipelineStageConfig, StoreConfig,
)
from selfheal.engine import SelfHealEngine
from selfheal.events import (
    ClassificationEvent, ErrorSeverity, PatchEvent,
    TestFailureEvent, ValidationEvent,
)
from selfheal.core.stores.memory_store import MemoryStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _failure(path="tests/test_calc.py::test_foo"):
    return TestFailureEvent(test_path=path, error_type="AssertionError",
                            error_message="assert 1 == 2", traceback="...")

def _classification(event=None):
    return ClassificationEvent(original_event=event or _failure(),
                               category="assertion", severity=ErrorSeverity.MEDIUM,
                               confidence=0.8)

def _patch(cls=None, pid=None, **kw):
    return PatchEvent(classification_event=cls or _classification(),
                      patch_id=pid or str(uuid.uuid4()),
                      patch_content=kw.pop("content", "# fix"),
                      generator=kw.pop("gen", "template"),
                      target_file=kw.pop("target", None), **kw)

def _passed(p):
    return ValidationEvent(patch_event=p, result="passed", duration=0.5)

def _failed(p):
    return ValidationEvent(patch_event=p, result="failed",
                            error_message="test still fails", duration=0.3)

_INIT_PATCHES = [
    patch.object(SelfHealEngine, "_setup_components"),
    patch.object(SelfHealEngine, "_setup_watchers"),
    patch.object(SelfHealEngine, "_setup_reporters"),
    patch.object(SelfHealEngine, "_setup_plugin_watcher"),
]

def _engine(config=None, extra_patches=None):
    """Create engine with real pipeline stages, mocked components."""
    all_p = _INIT_PATCHES + (extra_patches or [])
    for p in all_p:
        p.start()
    try:
        return SelfHealEngine(config or Config())
    finally:
        for p in all_p:
            p.stop()

def _setup(eng):
    """Set up mocked components: classifier, patcher, validator, reporter, store."""
    eng.classifier = MagicMock()
    eng.classifier.classify.return_value = _classification()
    eng.patcher = MagicMock()
    eng.patcher.generate.return_value = _patch()
    eng.validator = MagicMock()
    eng.validator.validate.return_value = _passed(eng.patcher.generate.return_value)
    eng.reporter = MagicMock()
    eng.store = MagicMock()

@pytest.fixture
def tmp_proj():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        src = root / "calc.py"
        src.write_text("def add(a, b):\n    return a + b\n")
        tdir = root / "tests"; tdir.mkdir()
        tf = tdir / "test_calc.py"
        tf.write_text("from calc import add\n\ndef test_add():\n    assert add(1,2)==3\n")
        yield {"root": root, "source": src, "test": tf}


# ===================================================================
# 1. Happy path – all 5 stages
# ===================================================================

class TestHappyPath:
    def test_all_5_stages_passed(self):
        eng = _engine(); _setup(eng)
        r = eng.process_failure(_failure())
        assert r.result == "passed"
        eng.reporter.report.assert_called_once()
        eng.store.save_events.assert_called_once()

    def test_event_chain_intact(self):
        eng = _engine()
        ev = _failure(); cls = _classification(ev); p = _patch(cls); ps = _passed(p)
        eng.classifier = MagicMock(); eng.classifier.classify.return_value = cls
        eng.patcher = MagicMock(); eng.patcher.generate.return_value = p
        eng.validator = MagicMock(); eng.validator.validate.return_value = ps
        eng.reporter = MagicMock(); eng.store = MagicMock()
        r = eng.process_failure(ev)
        assert r.patch_event.classification_event.original_event is ev
        assert r.patch_event.classification_event.category == "assertion"

    def test_failed_validation_flows(self):
        eng = _engine()
        p = _patch(); f = _failed(p)
        eng.classifier = MagicMock(); eng.classifier.classify.return_value = _classification()
        eng.patcher = MagicMock(); eng.patcher.generate.return_value = p
        eng.validator = MagicMock(); eng.validator.validate.return_value = f
        eng.reporter = MagicMock(); eng.store = MagicMock()
        r = eng.process_failure(_failure())
        assert r.result == "failed"
        eng.reporter.report.assert_called_once_with(r)

    def test_saved_events_contain_all_types(self):
        eng = _engine(); _setup(eng)
        eng.process_failure(_failure())
        saved = eng.store.save_events.call_args[0][0]
        names = [type(e).__name__ for e in saved]
        for t in ("TestFailureEvent", "ClassificationEvent", "PatchEvent", "ValidationEvent"):
            assert t in names


# ===================================================================
# 2. Auto-apply mode
# ===================================================================

class TestAutoApply:
    def test_writes_target_file(self, tmp_proj):
        cfg = Config(engine=EngineConfig(max_retries=1, retry_delay=0,
                     auto_apply=True, strategy_fallback=False))
        eng = _engine(cfg)
        p = _patch(content="def add(a,b):\n    return a*b\n", target=str(tmp_proj["source"]))
        eng.classifier = MagicMock(); eng.classifier.classify.return_value = _classification()
        eng.patcher = MagicMock(); eng.patcher.generate.return_value = p
        eng.validator = MagicMock(); eng.validator.validate.return_value = _passed(p)
        eng.reporter = MagicMock(); eng.store = MagicMock()
        eng.process_failure(_failure(str(tmp_proj["test"])))
        assert "a*b" in tmp_proj["source"].read_text()
        assert p.status == "applied"

    def test_creates_backup(self, tmp_proj):
        cfg = Config(engine=EngineConfig(max_retries=1, retry_delay=0,
                     auto_apply=True, strategy_fallback=False))
        eng = _engine(cfg)
        orig = tmp_proj["source"].read_text()
        p = _patch(content="def add(a,b):\n    return a/b\n", target=str(tmp_proj["source"]))
        eng.classifier = MagicMock(); eng.classifier.classify.return_value = _classification()
        eng.patcher = MagicMock(); eng.patcher.generate.return_value = p
        eng.validator = MagicMock(); eng.validator.validate.return_value = _passed(p)
        eng.reporter = MagicMock(); eng.store = MagicMock()
        eng.process_failure(_failure(str(tmp_proj["test"])))
        assert p.backup_path is not None
        assert Path(p.backup_path).read_text() == orig

    def test_rollback_restores(self, tmp_proj):
        cfg = Config(engine=EngineConfig(max_retries=1, retry_delay=0,
                     auto_apply=True, strategy_fallback=False))
        eng = _engine(cfg)
        orig = tmp_proj["source"].read_text()
        p = _patch(content="def add(a,b):\n    return a-b\n", target=str(tmp_proj["source"]))
        eng.classifier = MagicMock(); eng.classifier.classify.return_value = _classification()
        eng.patcher = MagicMock(); eng.patcher.generate.return_value = p
        eng.validator = MagicMock(); eng.validator.validate.return_value = _passed(p)
        eng.reporter = MagicMock(); eng.store = MagicMock()
        eng.process_failure(_failure(str(tmp_proj["test"])))
        assert tmp_proj["source"].read_text() != orig
        assert eng.applier.rollback(p) is True
        assert tmp_proj["source"].read_text() == orig
        assert p.status == "rolled_back"


# ===================================================================
# 3. Stage error resilience
# ===================================================================

class TestErrorResilience:
    def test_classify_error(self):
        eng = _engine()
        eng.classifier = MagicMock(); eng.classifier.classify.side_effect = RuntimeError("boom")
        eng.patcher = MagicMock(); eng.validator = MagicMock()
        eng.reporter = MagicMock(); eng.store = MagicMock()
        r = eng.process_failure(_failure())
        assert r.result == "error"
        eng.reporter.report.assert_called_once()

    def test_validate_error(self):
        eng = _engine()
        eng.classifier = MagicMock(); eng.classifier.classify.return_value = _classification()
        eng.patcher = MagicMock(); eng.patcher.generate.return_value = _patch()
        eng.validator = MagicMock(); eng.validator.validate.side_effect = RuntimeError("boom")
        eng.reporter = MagicMock(); eng.store = MagicMock()
        r = eng.process_failure(_failure())
        assert r.result == "error"

    def test_reporter_crash_no_block_store(self):
        eng = _engine()
        eng.classifier = MagicMock(); eng.classifier.classify.return_value = _classification()
        eng.patcher = MagicMock(); eng.patcher.generate.return_value = _patch()
        eng.validator = MagicMock(); eng.validator.validate.return_value = _passed(eng.patcher.generate.return_value)
        eng.reporter = MagicMock(); eng.reporter.report.side_effect = RuntimeError("boom")
        eng.store = MagicMock()
        r = eng.process_failure(_failure())
        assert r.result == "passed"
        eng.store.save_events.assert_called_once()


# ===================================================================
# 4. Custom pipeline config
# ===================================================================

class TestCustomPipeline:
    def test_disable_report(self):
        cfg = Config(pipeline=PipelineConfig(stages=[
            PipelineStageConfig(type="classify"), PipelineStageConfig(type="patch", retry=1),
            PipelineStageConfig(type="validate"), PipelineStageConfig(type="report", enabled=False),
            PipelineStageConfig(type="store"),
        ]))
        eng = _engine(cfg); _setup(eng)
        eng.process_failure(_failure())
        eng.reporter.report.assert_not_called()
        eng.store.save_events.assert_called_once()

    def test_disable_store(self):
        cfg = Config(pipeline=PipelineConfig(stages=[
            PipelineStageConfig(type="classify"), PipelineStageConfig(type="patch", retry=1),
            PipelineStageConfig(type="validate"), PipelineStageConfig(type="report"),
            PipelineStageConfig(type="store", enabled=False),
        ]))
        eng = _engine(cfg); _setup(eng)
        eng.process_failure(_failure())
        eng.reporter.report.assert_called_once()
        eng.store.save_events.assert_not_called()

    def test_no_validate_stage(self):
        cfg = Config(pipeline=PipelineConfig(stages=[
            PipelineStageConfig(type="classify"), PipelineStageConfig(type="patch", retry=1),
        ]))
        eng = _engine(cfg)
        eng.classifier = MagicMock(); eng.classifier.classify.return_value = _classification()
        eng.patcher = MagicMock(); eng.patcher.generate.return_value = _patch()
        eng.validator = MagicMock(); eng.reporter = MagicMock(); eng.store = MagicMock()
        r = eng.process_failure(_failure())
        assert r.result == "error"
        assert "Pipeline did not produce a result" in r.error_message
        eng.reporter.report.assert_not_called()


# ===================================================================
# 5. Multi-reporter chain
# ===================================================================

class TestMultiReporter:
    def test_two_reporters_called(self):
        eng = _engine(); _setup(eng)
        r1, r2 = MagicMock(), MagicMock()
        eng._reporters = [r1, r2]
        eng.reporter = MagicMock()  # fallback (unused when _reporters set)
        result = eng.process_failure(_failure())
        r1.report.assert_called_once_with(result)
        r2.report.assert_called_once_with(result)
        eng.reporter.report.assert_not_called()

    def test_one_crash_next_ok(self):
        eng = _engine(); _setup(eng)
        r1 = MagicMock(); r1.report.side_effect = RuntimeError("crash")
        r2 = MagicMock()
        eng._reporters = [r1, r2]
        eng.process_failure(_failure())
        r1.report.assert_called_once()
        r2.report.assert_called_once()


# ===================================================================
# 6. Store persistence
# ===================================================================

class TestStorePersistence:
    def test_memory_store_full_chain(self):
        eng = _engine(Config(engine=EngineConfig(max_retries=1, retry_delay=0)))
        failure = _failure("tests/test_auth.py::test_login")
        cls = _classification(failure); p = _patch(cls); ps = _passed(p)
        eng.classifier = MagicMock(); eng.classifier.classify.return_value = cls
        eng.patcher = MagicMock(); eng.patcher.generate.return_value = p
        eng.validator = MagicMock(); eng.validator.validate.return_value = ps
        eng.reporter = MagicMock()
        store = MemoryStore(StoreConfig(type="memory"))
        eng.store = store
        eng.process_failure(failure)
        assert len(store.get_events("failure")) == 1
        assert len(store.get_events("classification")) == 1
        assert len(store.get_events("patch")) == 1
        assert len(store.get_events("validation")) == 1
        assert store.get_events("validation")[0].result == "passed"

    def test_multiple_runs_accumulate(self):
        eng = _engine()
        store = MemoryStore(StoreConfig(type="memory"))
        eng.store = store
        eng.reporter = MagicMock()
        for i in range(3):
            ev = _failure(f"tests/test_{i}.py::test_x")
            eng.classifier = MagicMock(); eng.classifier.classify.return_value = _classification(ev)
            eng.patcher = MagicMock(); eng.patcher.generate.return_value = _patch()
            eng.validator = MagicMock(); eng.validator.validate.return_value = _passed(eng.patcher.generate.return_value)
            eng.process_failure(ev)
        assert len(store.get_events("failure")) == 3
        assert len(store.get_events("validation")) == 3


# ===================================================================
# 7. Metrics accumulation
# ===================================================================

class TestMetrics:
    def test_accumulate_across_runs(self):
        eng = _engine(); _setup(eng)
        eng.process_failure(_failure())
        eng.process_failure(_failure())
        assert eng.metrics.total_failures == 2
        assert eng.metrics.pipeline_runs == 2
        assert eng.metrics.success_count == 2

    def test_retry_metrics(self):
        cfg = Config(engine=EngineConfig(max_retries=3, retry_delay=0, strategy_fallback=False))
        eng = _engine(cfg)
        eng.classifier = MagicMock(); eng.classifier.classify.return_value = _classification()
        eng.patcher = MagicMock(); eng.patcher.generate.return_value = _patch()
        eng.validator = MagicMock(); eng.validator.validate.return_value = _failed(eng.patcher.generate.return_value)
        eng.reporter = MagicMock(); eng.store = MagicMock()
        eng.process_failure(_failure())
        assert eng.metrics.total_retries == 2  # 3 attempts → 2 retries
        assert eng.metrics.total_failures == 1
        assert eng.metrics.pipeline_runs == 1

    def test_classification_metrics(self):
        eng = _engine()
        eng.classifier = MagicMock()
        eng.classifier.classify.return_value = ClassificationEvent(
            original_event=_failure(), category="import",
            severity=ErrorSeverity.HIGH, confidence=0.9)
        eng.patcher = MagicMock(); eng.patcher.generate.return_value = _patch()
        eng.validator = MagicMock(); eng.validator.validate.return_value = _passed(eng.patcher.generate.return_value)
        eng.reporter = MagicMock(); eng.store = MagicMock()
        eng.process_failure(_failure())
        assert eng.metrics.classifications.get("import") == 1
        assert eng.metrics.severities.get(ErrorSeverity.HIGH.value) == 1

    def test_summary_keys(self):
        eng = _engine(); _setup(eng)
        eng.process_failure(_failure())
        s = eng.metrics.summary()
        for k in ("total_failures", "total_retries", "pipeline_runs",
                  "fix_rate_pct", "avg_validation_time_s", "avg_pipeline_time_s",
                  "generated_at"):
            assert k in s


# ===================================================================
# 8. Plugin integrity gate
# ===================================================================

class TestPluginGate:
    def test_violation_returns_error(self):
        all_p = _INIT_PATCHES + [
            patch.object(SelfHealEngine, "_check_integrity_before_failure", return_value=False),
        ]
        for p in all_p: p.start()
        try:
            eng = SelfHealEngine(Config())
        finally:
            for p in _INIT_PATCHES: p.stop()  # only stop init patches
        # _check_integrity_before_failure patch stays active
        eng.classifier = MagicMock(); eng.patcher = MagicMock()
        eng.validator = MagicMock(); eng.reporter = MagicMock(); eng.store = MagicMock()
        r = eng.process_failure(_failure())
        all_p[-1].stop()  # cleanup
        assert r.result == "error"
        assert "integrity violation" in r.error_message.lower()
        assert r.patch_event.patch_id == "integrity-violation"
        eng.classifier.classify.assert_not_called()

    def test_ok_proceeds(self):
        all_p = _INIT_PATCHES + [
            patch.object(SelfHealEngine, "_check_integrity_before_failure", return_value=True),
        ]
        for p in all_p: p.start()
        try:
            eng = SelfHealEngine(Config())
        finally:
            for p in _INIT_PATCHES: p.stop()
        eng.classifier = MagicMock(); eng.classifier.classify.return_value = _classification()
        eng.patcher = MagicMock(); eng.patcher.generate.return_value = _patch()
        eng.validator = MagicMock(); eng.validator.validate.return_value = _passed(eng.patcher.generate.return_value)
        eng.reporter = MagicMock(); eng.store = MagicMock()
        r = eng.process_failure(_failure())
        all_p[-1].stop()  # cleanup
        assert r.result == "passed"
        eng.classifier.classify.assert_called_once()


# ===================================================================
# 9. Strategy fallback
# ===================================================================

class TestStrategyFallback:
    def test_retries_on_apply_failure(self, tmp_proj):
        cfg = Config(engine=EngineConfig(max_retries=3, retry_delay=0,
                     auto_apply=True, strategy_fallback=True))
        eng = _engine(cfg)
        eng.classifier = MagicMock(); eng.classifier.classify.return_value = _classification()
        eng.validator = MagicMock(); eng.validator.validate.return_value = _passed(_patch())
        eng.reporter = MagicMock(); eng.store = MagicMock()

        p1 = _patch(content="# fix v1", target=str(tmp_proj["source"]))
        p2 = _patch(content="def add(a,b):\n    return a+b+1\n", target=str(tmp_proj["source"]))
        eng.patcher = MagicMock()
        eng.patcher.generate.side_effect = [p1, p2, p2]

        # Make first apply fail
        orig_apply = eng.applier.apply
        call_idx = {"n": 0}
        def apply_first_fail(patch):
            call_idx["n"] += 1
            if call_idx["n"] == 1:
                return False
            return orig_apply(patch)
        eng.applier.apply = MagicMock(side_effect=apply_first_fail)

        eng.process_failure(_failure(str(tmp_proj["test"])))
        assert eng.patcher.generate.call_count >= 2
        assert eng.metrics.total_retries >= 1


# ===================================================================
# 10. Context flow + batch
# ===================================================================

class TestContextFlow:
    def test_hook_sees_all_stages(self):
        stages_seen = []
        class SHook:
            def before_stage(self, n, c, e): stages_seen.append(("before", n))
            def after_stage(self, n, c, e, error=None): stages_seen.append(("after", n))
        h = SHook()
        all_p = _INIT_PATCHES[:]
        for p in all_p: p.start()
        try: eng = SelfHealEngine(Config(), hooks=[h])
        finally:
            for p in all_p: p.stop()
        _setup(eng)
        eng.process_failure(_failure())
        for s in ("classify", "patch", "validate", "report", "store"):
            assert ("before", s) in stages_seen
            assert ("after", s) in stages_seen

    def test_batch_independent_runs(self):
        eng = _engine(); _setup(eng)
        failures = [_failure(f"tests/test_{i}.py::test_x") for i in range(5)]
        results = eng.process_batch(failures)
        assert len(results) == 5
        assert all(r.result == "passed" for r in results)
        assert eng.metrics.total_failures == 5
        assert eng.metrics.pipeline_runs == 5
