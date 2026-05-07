"""Patch strategy for ASSERTION errors."""

from selfheal.events import ErrorCategory
from .base import TemplateRenderStrategy


class AssertionStrategy(TemplateRenderStrategy):
    """Generates patches for assertion errors using Jinja2 templates."""

    category = ErrorCategory.ASSERTION
