"""Tests for pipeline stage severity-based skip (P3-2)."""

from unittest.mock import MagicMock

import pytest

from selfheal.config import Config, PipelineConfig, PipelineStageConfig
from selfheal.engine import SelfHealEngine
from selfheal.events import ErrorSeverity
from tests.conftest import create_mock_engine, make_classification, make_failure


# ---------------------------------------------------------------------------
# _should_skip_stage unit tests
# ---------------------------------------------------------------------------

class TestShouldSkipStage:
    """Unit tests for SelfHealEngine._should_skip_stage()."""

    def test_no_stage_config_returns_false(self, mock_engine):
        """Returns False when stage has no _stage_config attached."""
        mock_stage = MagicMock()
        mock_stage.name = "some_stage"
        del mock_stage._stage_config  # Ensure no config
        # Directly test the method
        result = mock_engine._should_skip_stage(mock_stage, {})
        assert result is False

    def test_no_threshold_configured_returns_false(self, mock_engine):
        """Returns False when skip_if_severity_below is None."""
        stage_cfg = PipelineStageConfig(type="validate")
        mock_stage = MagicMock()
        mock_stage._stage_config = stage_cfg

        result = mock_engine._should_skip_stage(mock_stage, {})
        assert result is False

    def test_no_classification_in_context_returns_false(self, mock_engine):
        """Returns False when context has no classification (before classify stage)."""
        stage_cfg = PipelineStageConfig(type="patch", skip_if_severity_below="high")
        mock_stage = MagicMock()
        mock_stage._stage_config = stage_cfg

        result = mock_engine._should_skip_stage(mock_stage, {"event": make_failure()})
        assert result is False

    def test_severity_above_threshold_returns_false(self, mock_engine):
        """Returns False when severity >= threshold (should NOT skip)."""
        stage_cfg = PipelineStageConfig(type="validate", skip_if_severity_below="medium")
        mock_stage = MagicMock()
        mock_stage._stage_config = stage_cfg

        # severity=high >= threshold=medium -> should NOT skip
        classification = make_classification(severity=ErrorSeverity.HIGH)
        result = mock_engine._should_skip_stage(
            mock_stage, {"classification": {"severity": classification.severity}}
        )
        assert result is False

    def test_severity_equal_to_threshold_returns_false(self, mock_engine):
        """Returns False when severity == threshold (should NOT skip)."""
        stage_cfg = PipelineStageConfig(type="validate", skip_if_severity_below="medium")
        mock_stage = MagicMock()
        mock_stage._stage_config = stage_cfg

        classification = make_classification(severity=ErrorSeverity.MEDIUM)
        result = mock_engine._should_skip_stage(
            mock_stage, {"classification": {"severity": classification.severity}}
        )
        assert result is False

    def test_severity_below_threshold_returns_true(self, mock_engine):
        """Returns True when severity < threshold (SHOULD skip)."""
        stage_cfg = PipelineStageConfig(type="validate", skip_if_severity_below="high")
        mock_stage = MagicMock()
        mock_stage._stage_config = stage_cfg

        # severity=low < threshold=high -> should skip
        classification = make_classification(severity=ErrorSeverity.LOW)
        result = mock_engine._should_skip_stage(
            mock_stage, {"classification": {"severity": classification.severity}}
        )
        assert result is True

    def test_critical_threshold_skips_all_but_critical(self, mock_engine):
        """threshold=critical skips everything except CRITICAL severity."""
        stage_cfg = PipelineStageConfig(type="validate", skip_if_severity_below="critical")
        mock_stage = MagicMock()
        mock_stage._stage_config = stage_cfg

        # LOW/Critical should be skipped
        for sev in [ErrorSeverity.LOW, ErrorSeverity.MEDIUM, ErrorSeverity.HIGH]:
            classification = make_classification(severity=sev)
            assert mock_engine._should_skip_stage(
                mock_stage, {"classification": {"severity": classification.severity}}
            ) is True, f"Expected skip for {sev}"

        # CRITICAL should run
        classification = make_classification(severity=ErrorSeverity.CRITICAL)
        assert mock_engine._should_skip_stage(
            mock_stage, {"classification": {"severity": classification.severity}}
        ) is False

    def test_low_threshold_never_skips(self, mock_engine):
        """threshold=low means everything runs (nothing is below 'low')."""
        stage_cfg = PipelineStageConfig(type="validate", skip_if_severity_below="low")
        mock_stage = MagicMock()
        mock_stage._stage_config = stage_cfg

        for sev in ErrorSeverity:
            classification = make_classification(severity=sev)
            assert mock_engine._should_skip_stage(
                mock_stage, {"classification": {"severity": classification.severity}}
            ) is False, f"Expected no skip for {sev}"

    def test_severity_from_event_object_not_dict(self, mock_engine):
        """Handles classification as object with .severity attribute."""
        stage_cfg = PipelineStageConfig(type="validate", skip_if_severity_below="high")
        mock_stage = MagicMock()
        mock_stage._stage_config = stage_cfg

        classification = make_classification(severity=ErrorSeverity.LOW)
        result = mock_engine._should_skip_stage(
            mock_stage, {"classification": classification}
        )
        assert result is True

    def test_invalid_threshold_raises_validation_error(self):
        """Invalid threshold raises Pydantic ValidationError at config-creation time."""
        with pytest.raises(ValueError, match="Invalid severity threshold"):
            PipelineStageConfig(type="validate", skip_if_severity_below="invalid_value")


# ---------------------------------------------------------------------------
# Pipeline integration tests with severity skip
# ---------------------------------------------------------------------------

class TestPipelineSeveritySkipIntegration:
    """Integration tests verifying severity skip in full pipeline flow."""

    def test_patch_stage_skipped_for_low_severity(self):
        """patch stage with skip_if_severity_below=medium is skipped for low severity."""
        config = Config(
            pipeline=PipelineConfig(stages=[
                PipelineStageConfig(type="classify"),
                PipelineStageConfig(type="patch", retry=1, skip_if_severity_below="medium"),
                PipelineStageConfig(type="report"),
                PipelineStageConfig(type="store"),
            ])
        )
        engine = create_mock_engine(config)

        # Mock components
        engine.classifier = MagicMock()
        engine.classifier.classify.return_value = make_classification(severity=ErrorSeverity.LOW)
        engine.patcher = MagicMock()
        engine.patcher.generate.return_value = MagicMock()
        engine.validator = MagicMock()
        engine.reporter = MagicMock()
        engine.store = MagicMock()

        event = make_failure()
        engine.process_failure(event)

        # patch stage should have been skipped, so generate never called
        engine.patcher.generate.assert_not_called()

    def test_patch_stage_runs_for_high_severity(self):
        """patch stage with skip_if_severity_below=medium runs for CRITICAL severity."""
        config = Config(
            pipeline=PipelineConfig(stages=[
                PipelineStageConfig(type="classify"),
                PipelineStageConfig(type="patch", retry=1, skip_if_severity_below="medium"),
                PipelineStageConfig(type="report"),
                PipelineStageConfig(type="store"),
            ])
        )
        engine = create_mock_engine(config)

        engine.classifier = MagicMock()
        engine.classifier.classify.return_value = make_classification(severity=ErrorSeverity.CRITICAL)
        engine.patcher = MagicMock()
        engine.patcher.generate.return_value = MagicMock()
        engine.validator = MagicMock()
        engine.reporter = MagicMock()
        engine.store = MagicMock()

        event = make_failure()
        engine.process_failure(event)

        # patch stage should have been called (retry=1 means called at least once)
        assert engine.patcher.generate.call_count >= 1

    def test_validate_skipped_for_medium_on_high_threshold(self):
        """validate skipped when threshold=high and severity=medium."""
        config = Config(
            pipeline=PipelineConfig(stages=[
                PipelineStageConfig(type="classify"),
                PipelineStageConfig(type="patch", retry=1),
                PipelineStageConfig(type="validate", skip_if_severity_below="high"),
                PipelineStageConfig(type="report"),
                PipelineStageConfig(type="store"),
            ])
        )
        engine = create_mock_engine(config)

        engine.classifier = MagicMock()
        engine.classifier.classify.return_value = make_classification(severity=ErrorSeverity.MEDIUM)
        engine.patcher = MagicMock()
        engine.patcher.generate.return_value = MagicMock()
        engine.validator = MagicMock()
        engine.reporter = MagicMock()
        engine.store = MagicMock()

        event = make_failure()
        engine.process_failure(event)

        # validate should have been skipped
        engine.validator.validate.assert_not_called()

    def test_report_and_store_always_run(self):
        """report and store stages without skip_if_severity_below always execute."""
        from selfheal.events import ValidationEvent

        config = Config(
            pipeline=PipelineConfig(stages=[
                PipelineStageConfig(type="classify"),
                PipelineStageConfig(type="patch", retry=1),
                PipelineStageConfig(type="validate"),
                PipelineStageConfig(type="report"),
                PipelineStageConfig(type="store"),
            ])
        )
        engine = create_mock_engine(config)

        engine.classifier = MagicMock()
        engine.classifier.classify.return_value = make_classification(severity=ErrorSeverity.LOW)
        engine.patcher = MagicMock()
        engine.patcher.generate.return_value = MagicMock()
        engine.validator = MagicMock()
        engine.validator.validate.return_value = ValidationEvent(
            patch_event=MagicMock(), result="passed"
        )
        engine.reporter = MagicMock()
        engine.store = MagicMock()

        event = make_failure()
        engine.process_failure(event)

        # report and store should always be called (no skip configured)
        engine.reporter.report.assert_called_once()
        engine.store.save_events.assert_called_once()


# ---------------------------------------------------------------------------
# PipelineStageConfig validation tests
# ---------------------------------------------------------------------------

class TestPipelineStageConfigValidation:
    """Tests for PipelineStageConfig validator."""

    def test_default_skip_is_none(self):
        """Default skip_if_severity_below is None."""
        cfg = PipelineStageConfig(type="patch")
        assert cfg.skip_if_severity_below is None

    def test_valid_threshold_accepted(self):
        """Valid severity thresholds are accepted."""
        for val in ["low", "medium", "high", "critical"]:
            cfg = PipelineStageConfig(type="patch", skip_if_severity_below=val)
            assert cfg.skip_if_severity_below == val

    def test_case_insensitive(self):
        """Threshold is normalized to lowercase."""
        cfg = PipelineStageConfig(type="patch", skip_if_severity_below="HIGH")
        assert cfg.skip_if_severity_below == "high"

    def test_invalid_threshold_raises(self):
        """Invalid threshold raises ValueError."""
        with pytest.raises(ValueError, match="Invalid severity threshold"):
            PipelineStageConfig(type="patch", skip_if_severity_below="urgent")
