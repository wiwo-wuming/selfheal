"""Terminal reporter implementation."""

from typing import Optional

from selfheal.config import ReporterConfig
from selfheal.events import ValidationEvent
from selfheal.interfaces.reporter import ReporterInterface


class TerminalReporter(ReporterInterface):
    """Reports results to terminal with colors."""

    def __init__(self, config: ReporterConfig):
        self.config = config

    name = "terminal"

    def report(self, event: ValidationEvent) -> None:
        """Report validation event to terminal."""
        self._print_header()
        self._print_classification(event)
        self._print_patch(event)
        self._print_result(event)
        self._print_footer()

    def _print_header(self) -> None:
        """Print report header."""
        print("\n" + "=" * 60)
        print("  SelfHeal Report")
        print("=" * 60)

    def _print_classification(self, event: ValidationEvent) -> None:
        """Print classification details."""
        classification = event.patch_event.classification_event
        original = classification.original_event

        print("\n[Failure Details]")
        print(f"  Test: {original.test_path}")
        print(f"  Error: {original.error_type}")
        msg = original.error_message
        display_msg = msg[:100] + "..." if len(msg) > 100 else msg
        print(f"  Message: {display_msg}")

        print("\n[Classification]")
        print(f"  Category: {classification.category}")
        print(f"  Severity: {self._colorize_severity(classification.severity.value)}")
        print(f"  Confidence: {classification.confidence:.0%}")

        if classification.reasoning:
            print(f"  Reasoning: {classification.reasoning}")

    def _print_patch(self, event: ValidationEvent) -> None:
        """Print patch details with pending-review indicator."""
        patch = event.patch_event

        print("\n[Generated Patch]")
        print(f"  ID: {patch.patch_id}")
        print(f"  Generator: {patch.generator}")
        print(f"  Status: {self._format_patch_status(patch)}")

        if patch.target_file:
            print(f"  Target: {patch.target_file}")

        # Show review gate when patch was generated but not applied
        if patch.status in ("generated", "pending_review"):
            print(f"\n  WARNING: PATCH NOT AUTO-APPLIED (safety gate)")
            print(f"  Reason: auto_apply is disabled in engine config")
            print(f"  Action: Review the patch preview below, then apply manually")

        print(f"  Preview:")
        print("  " + "-" * 50)

        content_lines = patch.patch_content.split("\n")
        for line in content_lines[:10]:
            print(f"  {line}")

        if len(content_lines) > 10:
            print("  ...")

        print("  " + "-" * 50)

    @staticmethod
    def _format_patch_status(patch) -> str:
        """Colorize patch status for readability."""
        colors = {
            "generated": "\033[93mGENERATED (pending review)\033[0m",
            "pending_review": "\033[93mPENDING REVIEW\033[0m",
            "applied": "\033[92mAPPLIED\033[0m",
            "rejected": "\033[91mREJECTED\033[0m",
            "rolled_back": "\033[91mROLLED BACK\033[0m",
        }
        return colors.get(patch.status, patch.status.upper())

    def _print_result(self, event: ValidationEvent) -> None:
        """Print validation result."""
        status_color = self._get_status_color(event.result)
        status_text = event.result.upper()

        print(f"\n[{status_color}{status_text}{self._RESET}]")
        print(f"  Duration: {event.duration:.2f}s")

        if event.result == "failed":
            print(f"\n  Error Output:")
            output_lines = event.error_message.split("\n")[:5]
            for line in output_lines:
                print(f"    {line}")
        elif event.result == "passed":
            print("  All tests passed!")
        elif event.result == "error":
            print(f"  Error: {event.error_message[:200]}")

    def _print_footer(self) -> None:
        """Print report footer."""
        print("\n" + "=" * 60)
        print()

    def _colorize_severity(self, severity: str) -> str:
        """Colorize severity level."""
        colors = {
            "critical": f"{self._RED}{severity}{self._RESET}",
            "high": f"{self._ORANGE}{severity}{self._RESET}",
            "medium": f"{self._YELLOW}{severity}{self._RESET}",
            "low": f"{self._GREEN}{severity}{self._RESET}",
        }
        return colors.get(severity, severity)

    def _get_status_color(self, status: str) -> str:
        """Get color for status."""
        colors = {
            "passed": self._GREEN,
            "failed": self._RED,
            "error": self._ORANGE,
        }
        return colors.get(status, "")

    # ANSI color codes
    _RED = "\033[91m"
    _GREEN = "\033[92m"
    _YELLOW = "\033[93m"
    _ORANGE = "\033[38;5;208m"
    _RESET = "\033[0m"
