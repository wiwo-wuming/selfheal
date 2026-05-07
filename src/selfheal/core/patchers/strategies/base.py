"""Base classes for patch generation strategies."""

import logging
import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from selfheal.events import ClassificationEvent, PatchEvent, ErrorCategory

if TYPE_CHECKING:
    from selfheal.core.patchers.template_patcher import TemplatePatcher

logger = logging.getLogger(__name__)


class PatchStrategy(ABC):
    """Abstract base for category-specific patch generation strategies.

    Each strategy handles one error category by producing a unified-diff
    patch from Jinja2 templates or custom logic.
    """

    category: ErrorCategory

    @abstractmethod
    def generate(
        self, classification: ClassificationEvent, patcher: "TemplatePatcher"
    ) -> PatchEvent:
        """Generate a patch for the given classification."""
        ...


class TemplateRenderStrategy(PatchStrategy):
    """Strategy that renders a Jinja2 template to generate patches.

    Looks for ``{category}/default.py.j2``, falls back to ``_generic.py.j2``,
    and finally to the hardcoded fallback in ``_generate_fallback_patch``.
    """

    def generate(
        self, classification: ClassificationEvent, patcher: "TemplatePatcher"
    ) -> PatchEvent:
        category = classification.category
        templates_dir = patcher._templates_dir

        # Look for template in category subdirectory
        template_path = templates_dir / category / "default.py.j2"

        # Fall back to generic template
        if not template_path.exists():
            template_path = templates_dir / "_generic.py.j2"

        if not template_path.exists():
            logger.warning("No template found for category: %s", category)
            fallback_content, fallback_target = patcher._generate_fallback_patch(
                classification
            )
            return PatchEvent(
                classification_event=classification,
                patch_id=str(uuid.uuid4()),
                patch_content=fallback_content,
                generator="template",
                target_file=fallback_target,
            )

        try:
            env = patcher._get_env()
            rel_path = template_path.relative_to(templates_dir).as_posix()
            template = env.get_template(rel_path)
            ctx = patcher._build_template_context(classification)
            content = template.render(**ctx)
            return PatchEvent(
                classification_event=classification,
                patch_id=str(uuid.uuid4()),
                patch_content=content,
                generator="template",
                target_file=ctx.get("target_file"),
            )
        except Exception as e:
            logger.error("Template rendering failed: %s", e)
            fallback_content, fallback_target = patcher._generate_fallback_patch(
                classification
            )
            return PatchEvent(
                classification_event=classification,
                patch_id=str(uuid.uuid4()),
                patch_content=fallback_content,
                generator="template",
                target_file=fallback_target,
            )
