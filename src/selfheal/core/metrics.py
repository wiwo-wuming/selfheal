"""Metrics collection for SelfHeal."""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from selfheal.events import ErrorSeverity


class MetricsCollector:
    """Collects and reports pipeline metrics."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        """Reset all metrics counters."""
        self._start_time = time.time()
        self.total_failures: int = 0
        self.total_retries: int = 0
        self.classifications: dict[str, int] = defaultdict(int)
        self.severities: dict[str, int] = defaultdict(int)
        self.patch_results: dict[str, int] = defaultdict(int)
        self._pipeline_times: dict[str, list[float]] = defaultdict(list)
        self.validation_durations: list[float] = []
        self.pipeline_durations: list[float] = []
        self.success_count: int = 0
        self.failure_count: int = 0
        self.pipeline_runs: int = 0

    def record_failure(self) -> None:
        self.total_failures += 1

    def record_retry(self) -> None:
        self.total_retries += 1

    def record_classification(self, category: str, severity: str) -> None:
        self.classifications[category] += 1
        self.severities[severity] += 1

    def record_patch(self, status: str) -> None:
        self.patch_results[status] += 1

    def record_validation(self, result: str, duration: float) -> None:
        if result == "passed":
            self.success_count += 1
        else:
            self.failure_count += 1
        self.validation_durations.append(duration)

    def record_pipeline_run(self, duration: float) -> None:
        self.pipeline_runs += 1
        self.pipeline_durations.append(duration)

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self._start_time

    @property
    def fix_rate(self) -> float:
        """Percentage of validations that passed."""
        total = self.success_count + self.failure_count
        return (self.success_count / total * 100) if total > 0 else 0.0

    @property
    def avg_validation_time(self) -> float:
        return (
            sum(self.validation_durations) / len(self.validation_durations)
            if self.validation_durations else 0.0
        )

    @property
    def avg_pipeline_time(self) -> float:
        return (
            sum(self.pipeline_durations) / len(self.pipeline_durations)
            if self.pipeline_durations else 0.0
        )

    def summary(self) -> dict[str, Any]:
        """Return a summary dict of all metrics."""
        return {
            "uptime_seconds": round(self.uptime_seconds, 1),
            "total_failures": self.total_failures,
            "total_retries": self.total_retries,
            "pipeline_runs": self.pipeline_runs,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "fix_rate_pct": round(self.fix_rate, 1),
            "avg_validation_time_s": round(self.avg_validation_time, 2),
            "avg_pipeline_time_s": round(self.avg_pipeline_time, 2),
            "top_categories": dict(
                sorted(self.classifications.items(), key=lambda x: x[1], reverse=True)[:5]
            ),
            "top_severities": dict(
                sorted(self.severities.items(), key=lambda x: x[1], reverse=True)[:5]
            ),
            "patch_statuses": dict(self.patch_results),
            "generated_at": datetime.now().isoformat(),
        }

    def format_report(self) -> str:
        """Format metrics as a human-readable string."""
        s = self.summary()
        lines = [
            "=" * 50,
            "  SelfHeal Metrics Report",
            "=" * 50,
            f"  Uptime:              {s['uptime_seconds']}s",
            f"  Pipeline Runs:       {s['pipeline_runs']}",
            f"  Total Failures:      {s['total_failures']}",
            f"  Total Retries:       {s['total_retries']}",
            f"  Fix Rate:            {s['fix_rate_pct']}%",
            f"  Avg Pipeline Time:   {s['avg_pipeline_time_s']}s",
            f"  Avg Validation Time: {s['avg_validation_time_s']}s",
            "",
            "  Top Categories:",
        ]
        for cat, count in s["top_categories"].items():
            lines.append(f"    {cat}: {count}")

        lines += [
            "",
            "  Patch Statuses:",
        ]
        for status, count in s["patch_statuses"].items():
            lines.append(f"    {status}: {count}")

        lines.append("=" * 50)
        return "\n".join(lines)
