"""Patch strategy for RUNTIME, TYPE, VALUE, and SYNTAX errors."""

from selfheal.events import ErrorCategory

from .base import TemplateRenderStrategy


class RuntimeStrategy(TemplateRenderStrategy):
    """Generates patches for runtime-related errors using Jinja2 templates.

    Covers RUNTIME, TYPE, VALUE, and SYNTAX categories.
    """

    category: ErrorCategory = ErrorCategory.RUNTIME
