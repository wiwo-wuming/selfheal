"""Patch strategy for IMPORT errors with smart typo-aware builder."""

import uuid
from typing import TYPE_CHECKING

from selfheal.events import ClassificationEvent, ErrorCategory, PatchEvent

from .base import PatchStrategy

if TYPE_CHECKING:
    from selfheal.core.patchers.template_patcher import TemplatePatcher


class ImportStrategy(PatchStrategy):
    """Generates import-fix patches with typo correction and module detection.

    Three strategies (in order of preference):

    1. **Typo fix**: if Python suggested a correction, fix the typo
       in the existing import line.
    2. **Missing sub-module**: add ``from source import missing``.
    3. **Missing top-level**: add ``import missing_module``.
    """

    category: ErrorCategory = ErrorCategory.IMPORT

    def generate(
        self, classification: ClassificationEvent, patcher: "TemplatePatcher"
    ) -> PatchEvent:
        # Lazy import to avoid circular dependency
        from selfheal.core.patchers.template_patcher import (
            _parse_error_message,
            _parse_traceback,
        )

        event = classification.original_event
        tb_info = _parse_traceback(event.traceback)
        err_info = _parse_error_message(event.error_message, event.error_type)
        target_file = tb_info.get("error_file") or event.test_path
        error_line = tb_info.get("error_line") or 1
        original_code = tb_info.get("original_code") or ""

        content = patcher._build_import_patch(
            str(target_file),
            error_line,
            original_code,
            err_info.get("typo_suggestion"),
            err_info.get("missing_module", "unknown_module"),
            err_info.get("source_module"),
        )

        ctx = patcher._build_template_context(classification)
        return PatchEvent(
            classification_event=classification,
            patch_id=str(uuid.uuid4()),
            patch_content=content,
            generator="template",
            target_file=ctx.get("target_file"),
        )
