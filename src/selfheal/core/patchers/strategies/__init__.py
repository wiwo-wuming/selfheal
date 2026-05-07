"""Strategy registry for template patcher.

Registers PatchStrategy subclasses and dispatches patch generation
by error category.
"""

from typing import Optional

from .base import PatchStrategy

_strategies: dict[str, PatchStrategy] = {}


def register_strategy(strategy: PatchStrategy) -> None:
    """Register a patch strategy for its declared error category."""
    _strategies[strategy.category.value] = strategy


def get_strategy(category: str) -> Optional[PatchStrategy]:
    """Return the registered strategy for *category*, or None."""
    return _strategies.get(category)


# ---------------------------------------------------------------------------
# Import and register all strategies
# ---------------------------------------------------------------------------
from .assertion import AssertionStrategy
from .import_strategy import ImportStrategy
from .runtime import RuntimeStrategy
from .fallback import FallbackStrategy

from selfheal.events import ErrorCategory

# Single-category strategies
register_strategy(AssertionStrategy())
register_strategy(ImportStrategy())

# RuntimeStrategy handles RUNTIME, TYPE, VALUE, SYNTAX
for cat in (
    ErrorCategory.RUNTIME,
    ErrorCategory.TYPE,
    ErrorCategory.VALUE,
    ErrorCategory.SYNTAX,
):
    s = RuntimeStrategy()
    s.category = cat
    register_strategy(s)

# FallbackStrategy handles all remaining categories
_fallback_categories = (
    ErrorCategory.TIMEOUT,
    ErrorCategory.NETWORK,
    ErrorCategory.CONFIG,
    ErrorCategory.DEPENDENCY,
    ErrorCategory.RESOURCE,
    ErrorCategory.PERMISSION,
    ErrorCategory.FLAKY,
    ErrorCategory.MEMORY,
    ErrorCategory.UNKNOWN,
)
for cat in _fallback_categories:
    s = FallbackStrategy()
    s.category = cat
    register_strategy(s)
