"""Tests for MetricsCollector."""

import pytest

from selfheal.core.metrics import MetricsCollector


class TestMetricsCollector:
    def test_initial_state(self):
        mc = MetricsCollector()
        assert mc.total_failures == 0
        assert mc.total_retries == 0
        assert mc.pipeline_runs == 0
        assert mc.fix_rate == 0.0

    def test_record_failure(self):
        mc = MetricsCollector()
        mc.record_failure()
        mc.record_failure()
        assert mc.total_failures == 2

    def test_record_retry(self):
        mc = MetricsCollector()
        mc.record_retry()
        mc.record_retry()
        mc.record_retry()
        assert mc.total_retries == 3

    def test_record_classification(self):
        mc = MetricsCollector()
        mc.record_classification("assertion", "high")
        mc.record_classification("assertion", "medium")
        mc.record_classification("import", "low")
        assert mc.classifications["assertion"] == 2
        assert mc.classifications["import"] == 1
        assert mc.severities["high"] == 1

    def test_fix_rate(self):
        mc = MetricsCollector()
        mc.record_validation("passed", 1.0)
        mc.record_validation("failed", 2.0)
        mc.record_validation("passed", 1.5)
        assert mc.fix_rate == pytest.approx(66.67, 0.01)
        assert mc.success_count == 2
        assert mc.failure_count == 1

    def test_avg_validation_time(self):
        mc = MetricsCollector()
        mc.record_validation("passed", 1.0)
        mc.record_validation("passed", 3.0)
        assert mc.avg_validation_time == pytest.approx(2.0)

    def test_avg_pipeline_time(self):
        mc = MetricsCollector()
        mc.record_pipeline_run(1.0)
        mc.record_pipeline_run(5.0)
        assert mc.avg_pipeline_time == pytest.approx(3.0)

    def test_summary(self):
        mc = MetricsCollector()
        mc.record_failure()
        mc.record_classification("assertion", "high")
        mc.record_validation("passed", 2.0)
        mc.record_pipeline_run(2.0)

        summary = mc.summary()
        assert "fix_rate_pct" in summary
        assert "top_categories" in summary
        assert summary["top_categories"]["assertion"] == 1
        assert summary["pipeline_runs"] == 1

    def test_format_report(self):
        mc = MetricsCollector()
        mc.record_failure()
        mc.record_classification("assertion", "high")
        mc.record_validation("passed", 2.0)
        mc.record_pipeline_run(2.0)

        report = mc.format_report()
        assert "SelfHeal Metrics Report" in report
        assert "Fix Rate" in report
        assert "assertion" in report

    def test_reset(self):
        mc = MetricsCollector()
        mc.record_failure()
        mc.record_validation("passed", 1.0)
        mc.reset()
        assert mc.total_failures == 0
        assert mc.success_count == 0

    def test_uptime(self):
        mc = MetricsCollector()
        assert mc.uptime_seconds >= 0
