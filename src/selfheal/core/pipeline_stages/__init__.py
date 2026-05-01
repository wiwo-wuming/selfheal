"""Built-in pipeline stage implementations."""

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
