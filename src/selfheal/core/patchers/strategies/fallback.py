"""Fallback patch strategy for all remaining error categories."""

from selfheal.events import ErrorCategory
from .base import TemplateRenderStrategy


class FallbackStrategy(TemplateRenderStrategy):
    """Generates patches for remaining categories using Jinja2 templates.

    Covers TIMEOUT, NETWORK, CONFIG, DEPENDENCY, RESOURCE, PERMISSION,
    FLAKY, MEMORY, and UNKNOWN categories.
    """

    category = ErrorCategory.UNKNOWN
