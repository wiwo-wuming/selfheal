"""SelfHeal - Intelligent Test Self-Healing Framework."""

__version__ = "0.1.0"

from selfheal.engine import SelfHealEngine
from selfheal.events import (
    TestFailureEvent,
    ClassificationEvent,
    PatchEvent,
    ValidationEvent,
)

__all__ = [
    "SelfHealEngine",
    "TestFailureEvent",
    "ClassificationEvent",
    "PatchEvent",
    "ValidationEvent",
]
