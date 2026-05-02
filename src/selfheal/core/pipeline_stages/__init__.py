"""Built-in pipeline stage implementations.

Each stage corresponds to one step in the self-healing pipeline:

* **ClassifyStage**: Uses the configured classifier to categorize a test failure.
* **PatchStage**: Generates one or more patches for a classified failure, with
  optional retry and strategy fallback.
* **ValidateStage**: Runs the validator against each patch and records
  successful results to the experience store.
* **ReportStage**: Sends validation results through the configured reporter.
* **StoreStage**: Persists the complete event chain to long-term storage.

All stages exchange data via a shared ``context`` dict. The engine passes
``engine`` itself in the context so stages can access configuration, metrics,
and other runtime services.
"""

from selfheal.core.pipeline_stages.classify_stage import ClassifyStage
from selfheal.core.pipeline_stages.patch_stage import PatchStage
from selfheal.core.pipeline_stages.validate_stage import ValidateStage
from selfheal.core.pipeline_stages.report_stage import ReportStage
from selfheal.core.pipeline_stages.store_stage import StoreStage

__all__ = [
    "ClassifyStage",
    "PatchStage",
    "ValidateStage",
    "ReportStage",
    "StoreStage",
]
