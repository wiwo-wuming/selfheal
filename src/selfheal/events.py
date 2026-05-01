"""Event definitions for SelfHeal."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class ErrorSeverity(Enum):
    """Error severity levels."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ErrorCategory(Enum):
    """Built-in error categories."""
    ASSERTION = "assertion"
    IMPORT = "import"
    TIMEOUT = "timeout"
    NETWORK = "network"
    SYNTAX = "syntax"
    RUNTIME = "runtime"
    CONFIG = "config"
    DEPENDENCY = "dependency"
    RESOURCE = "resource"
    PERMISSION = "permission"
    FLAKY = "flaky"
    VALUE = "value"
    TYPE = "type"
    MEMORY = "memory"
    UNKNOWN = "unknown"


@dataclass
class TestFailureEvent:
    """Event fired when a test fails."""
    __test__ = False  # Prevent pytest from treating this as a test class

    test_path: str
    error_type: str
    error_message: str
    traceback: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "test_path": self.test_path,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "traceback": self.traceback,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }


@dataclass
class ClassificationEvent:
    """Event fired after classification."""
    original_event: TestFailureEvent
    category: str
    severity: ErrorSeverity
    confidence: float  # 0.0 to 1.0
    reasoning: str = ""
    alternative_categories: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "original_event": self.original_event.to_dict(),
            "category": self.category,
            "severity": self.severity.value,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "alternative_categories": self.alternative_categories,
        }


@dataclass
class PatchEvent:
    """Event fired after patch generation."""
    classification_event: ClassificationEvent
    patch_id: str
    patch_content: str
    generator: str  # "template" or "llm"
    target_file: Optional[str] = None  # file path to apply patch to
    backup_path: Optional[str] = None  # backup of original file
    status: str = "generated"  # generated, pending_review, applied, rejected, rolled_back
    applied_at: Optional[datetime] = None
    suggested_command: str = ""  # CLI command hint for manual review mode

    def to_dict(self) -> dict:
        return {
            "classification_event": self.classification_event.to_dict(),
            "patch_id": self.patch_id,
            "patch_content": self.patch_content,
            "generator": self.generator,
            "target_file": self.target_file,
            "backup_path": self.backup_path,
            "status": self.status,
            "applied_at": self.applied_at.isoformat() if self.applied_at else None,
        }


@dataclass
class ValidationEvent:
    """Event fired after validation."""
    patch_event: PatchEvent
    result: str  # "passed", "failed", "error"
    test_output: str = ""
    duration: float = 0.0
    error_message: str = ""
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "patch_event": self.patch_event.to_dict(),
            "result": self.result,
            "test_output": self.test_output,
            "duration": self.duration,
            "error_message": self.error_message,
            "timestamp": self.timestamp.isoformat(),
        }
