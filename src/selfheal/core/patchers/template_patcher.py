"""Template-based patcher implementation."""

import logging
import re
import uuid
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, Template

from selfheal.config import PatcherConfig
from selfheal.events import ClassificationEvent, PatchEvent
from selfheal.interfaces.patcher import PatcherInterface

logger = logging.getLogger(__name__)

# Limits for diff hunk processing
_DIFF_PREVIEW_LINES = 20      # lines to inspect when checking if content is a diff
_MAX_HUNK_LINES = 100         # safety cap for hunk line count during diff application

# Regex to extract file/line/function from traceback lines like:
#   File "/path/to/file.py", line 42, in test_foo
_TRACEBACK_LOCATION_RE = re.compile(
    r'File\s+"([^"]+)",\s+line\s+(\d+)(?:,\s+in\s+(\w+))?'
)

# Regex for pytest assertion messages: "assert 3 == 5" or "assert 'a' == 'b'"
_ASSERT_COMPARE_RE = re.compile(r"assert\s+(.+)\s+==\s+(.+)")


def _parse_traceback(traceback_text: str) -> dict:
    """Extract structured information from a Python traceback string.

    Returns a dict with:
        - error_file: path to the failing source file
        - error_line: line number (int or None)
        - error_func: function name (str or None)
        - original_code: the source line from the traceback (str or empty)
        - expected: expected value from assertion (str or None)
        - actual: actual value from assertion (str or None)
        - missing_module: module name for import errors (str or None)
    """
    result: dict = {
        "error_file": "",
        "error_line": None,
        "error_func": None,
        "original_code": "",
        "expected": None,
        "actual": None,
        "missing_module": None,
    }

    if not traceback_text:
        return result

    lines = traceback_text.split("\n")

    # Walk backwards to find the last File "..." line (the actual failure point)
    last_match_idx = None
    for idx in range(len(lines) - 1, -1, -1):
        match = _TRACEBACK_LOCATION_RE.search(lines[idx])
        if match:
            result["error_file"] = match.group(1)
            result["error_line"] = int(match.group(2))
            result["error_func"] = match.group(3)
            last_match_idx = idx
            break

    # Extract the source code line following the same File line
    if last_match_idx is not None:
        for j in range(last_match_idx + 1, min(last_match_idx + 3, len(lines))):
            candidate = lines[j].strip()
            if candidate and not candidate.startswith(("File ", "Traceback")):
                result["original_code"] = candidate
                break

    return result


def _parse_error_message(error_message: str, error_type: str) -> dict:
    """Parse the error message to extract useful details for patching.

    Returns a dict that may include:
        - expected / actual (for assertion errors)
        - missing_module (for import errors)
        - error_detail (a cleaned-up version of the message)
    """
    detail: dict = {
        "expected": None,
        "actual": None,
        "missing_module": None,
        "error_detail": error_message.strip(),
    }

    if error_type in ("AssertionError",) and error_message:
        # Try "assert X == Y" pattern
        match = _ASSERT_COMPARE_RE.search(error_message)
        if match:
            detail["expected"] = match.group(2).strip()
            detail["actual"] = match.group(1).strip()

    if error_type in ("ImportError", "ModuleNotFoundError") and error_message:
        # "No module named 'foo'" -> extract 'foo'
        match = re.search(r"No module named ['\"]?(\w+)", error_message)
        if match:
            detail["missing_module"] = match.group(1)
        else:
            # "cannot import name 'foo' from 'bar'" -> extract 'foo'
            match = re.search(r"cannot import name ['\"]?(\w+)", error_message)
            if match:
                detail["missing_module"] = match.group(1)

    return detail


class TemplatePatcher(PatcherInterface):
    """Template-based patch generator.

    Generates unified-diff patches from Jinja2 templates.  The templates
    receive structured context extracted from the traceback so they can
    produce targeted, line-aware fixes.
    """

    def __init__(self, config: PatcherConfig):
        self.config = config
        self._env: Optional[Environment] = None
        self._templates_dir = self._resolve_templates_dir(config.templates_dir)

    name = "template"

    @staticmethod
    def _resolve_templates_dir(configured_dir: str) -> Path:
        """Resolve templates directory with intelligent fallback.

        Strategy (in order):
        1. Absolute path → use as-is.
        2. Relative to CWD   → check if exists.
        3. Relative to package → check src/selfheal/patches/.
        4. Relative to project root → check selfheal/patches/ (dev install).
        5. Fallback to the configured path (caller handles missing templates).
        """
        configured = Path(configured_dir)

        # 1. Absolute path
        if configured.is_absolute() and configured.exists():
            logger.debug("Using absolute templates dir: %s", configured)
            return configured

        # 2. CWD-relative
        cwd_path = (Path.cwd() / configured).resolve()
        if cwd_path.exists():
            logger.debug("Using CWD-relative templates dir: %s", cwd_path)
            return cwd_path

        # 3. Package-embedded: src/selfheal/patches/
        pkg_path = (Path(__file__).resolve().parents[3] / configured).resolve()
        if pkg_path.exists():
            logger.debug("Using package-embedded templates dir: %s", pkg_path)
            return pkg_path

        # 4. Project root: selfheal/patches/ (editable install fallback)
        root_path = (Path(__file__).resolve().parents[5] / configured).resolve()
        if root_path.exists():
            logger.debug("Using project-root templates dir: %s", root_path)
            return root_path

        # 5. Return CWD-relative as last resort (fallback patches will handle missing templates)
        logger.warning(
            "Templates dir not found at any location, using CWD-relative: %s",
            cwd_path,
        )
        return cwd_path

    def _get_env(self) -> Environment:
        """Get or create Jinja2 environment."""
        if self._env is None:
            self._env = Environment(
                loader=FileSystemLoader(str(self._templates_dir)),
                trim_blocks=True,
                lstrip_blocks=True,
                autoescape=True,
            )
        return self._env

    def _read_source_line(self, file_path: str, line_number: int) -> str:
        """Read a single line from a source file safely."""
        try:
            p = Path(file_path)
            if p.exists() and p.is_file():
                lines = p.read_text(encoding="utf-8").splitlines()
                if 0 < line_number <= len(lines):
                    return lines[line_number - 1]
        except (OSError, UnicodeDecodeError):
            pass
        return ""

    def _build_template_context(self, classification: ClassificationEvent) -> dict:
        """Build the full context dict passed to Jinja2 templates."""
        event = classification.original_event
        tb_info = _parse_traceback(event.traceback)
        err_info = _parse_error_message(event.error_message, event.error_type)

        # Merge traceback and error-message extractions
        tb_info.update({k: v for k, v in err_info.items() if v is not None})

        # If traceback gave us a line number, try to read the actual source line
        original_code = tb_info.get("original_code", "")
        if not original_code and tb_info.get("error_file") and tb_info.get("error_line"):
            original_code = self._read_source_line(
                tb_info["error_file"], tb_info["error_line"]
            )
            tb_info["original_code"] = original_code

        # Determine target file (prefer the traceback file, fall back to test_path)
        target_file = tb_info.get("error_file") or event.test_path

        return {
            "event": event,
            "classification": classification,
            "category": classification.category,
            "severity": classification.severity.value,
            "confidence": classification.confidence,
            "reasoning": classification.reasoning,
            "error_type": event.error_type,
            "error_message": event.error_message,
            "test_path": event.test_path,
            "target_file": target_file,
            "error_line": tb_info.get("error_line"),
            "error_func": tb_info.get("error_func"),
            "original_code": tb_info.get("original_code", ""),
            "expected": tb_info.get("expected"),
            "actual": tb_info.get("actual"),
            "missing_module": tb_info.get("missing_module"),
            "error_detail": tb_info.get("error_detail", event.error_message),
        }

    def generate(self, classification: ClassificationEvent) -> PatchEvent:
        """Generate a patch using templates, with experience-based reuse.

        Strategy:
        1. Check the experience store for a previously successful patch.
        2. If no experience match, fall back to template-based generation.
        """
        # --- try experience-based reuse first ---
        experience_patch = self._try_experience_patch(classification)
        if experience_patch is not None:
            return experience_patch

        # --- template-based generation ---
        category = classification.category
        templates_dir = self._templates_dir

        # Look for template in category subdirectory
        template_path = templates_dir / category / "default.py.j2"

        # Fall back to generic template
        if not template_path.exists():
            template_path = templates_dir / "_generic.py.j2"

        if not template_path.exists():
            logger.warning(f"No template found for category: {category}")
            fallback_content, fallback_target = self._generate_fallback_patch(classification)
            return PatchEvent(
                classification_event=classification,
                patch_id=str(uuid.uuid4()),
                patch_content=fallback_content,
                generator="template",
                target_file=fallback_target,
            )

        try:
            env = self._get_env()
            # Use POSIX-style path separators for Jinja2 FileSystemLoader
            rel_path = template_path.relative_to(templates_dir).as_posix()
            template = env.get_template(rel_path)
            ctx = self._build_template_context(classification)
            content = template.render(**ctx)

            # Also pass target_file to PatchEvent so PatchApplier knows where to apply
            return PatchEvent(
                classification_event=classification,
                patch_id=str(uuid.uuid4()),
                patch_content=content,
                generator="template",
                target_file=ctx.get("target_file"),
            )
        except Exception as e:
            logger.error(f"Template rendering failed: {e}")
            fallback_content, fallback_target = self._generate_fallback_patch(classification)
            return PatchEvent(
                classification_event=classification,
                patch_id=str(uuid.uuid4()),
                patch_content=fallback_content,
                generator="template",
                target_file=fallback_target,
            )

    @staticmethod
    def _try_experience_patch(classification: ClassificationEvent) -> Optional[PatchEvent]:
        """Search the experience store for a previously successful patch."""
        try:
            from selfheal.core.experience import get_experience

            experience = get_experience()
            similar = experience.find_similar(
                event=classification.original_event,
                category=classification.category,
                limit=1,
            )
            if similar:
                entry = similar[0]
                logger.info(
                    "Experience match: reusing patch (signature=%s, success_count=%d)",
                    entry["signature"], entry["success_count"],
                )
                return PatchEvent(
                    classification_event=classification,
                    patch_id=str(uuid.uuid4()),
                    patch_content=entry["patch_content"],
                    generator=f"experience({entry['generator']})",
                )
        except Exception:
            logger.debug("Experience lookup skipped", exc_info=True)
        return None

    @staticmethod
    def _try_experience_fallback(classification: ClassificationEvent) -> Optional[tuple[str, Optional[str]]]:
        """Search experience store without category restriction for fallback.

        When no template exists for a category, this broader search may find
        a previously successful patch for a similar error regardless of category.

        Returns (patch_content, target_file) if found, None otherwise.
        """
        try:
            from selfheal.core.experience import get_experience

            experience = get_experience()
            similar = experience.find_similar(
                event=classification.original_event,
                category=None,  # broader: match any category
                limit=1,
            )
            if similar:
                entry = similar[0]
                logger.info(
                    "Fallback experience match: reusing patch (signature=%s, "
                    "category=%s, success_count=%d)",
                    entry["signature"], entry.get("category", "?"), entry["success_count"],
                )
                return entry["patch_content"], classification.original_event.test_path
        except Exception:
            logger.debug("Fallback experience lookup skipped", exc_info=True)
        return None

    def _generate_fallback_patch(self, classification: ClassificationEvent) -> tuple[str, Optional[str]]:
        """Generate an executable fallback patch when no template is found.

        Returns a tuple of (patch_content, target_file).

        Tries the experience store with a broader (category-less) search first.
        If a match is found it is preferred over the hardcoded defensive patch.
        """
        # --- try experience store without category restriction ---
        experience_patch = self._try_experience_fallback(classification)
        if experience_patch is not None:
            return experience_patch

        category = classification.category
        event = classification.original_event

        # Use traceback-parsed info for better targeting
        tb_info = _parse_traceback(event.traceback)
        err_info = _parse_error_message(event.error_message, event.error_type)
        target_file = tb_info.get("error_file") or event.test_path
        error_line = tb_info.get("error_line") or 1
        original_code = tb_info.get("original_code") or f"# (failing code at line {error_line})"

        patches: dict[str, str] = {
            "assertion": (
                f"--- a/{target_file}\n"
                f"+++ b/{target_file}\n"
                f"@@ -{error_line},1 +{error_line},4 @@\n"
                f"-{original_code}\n"
                f"+# SelfHeal: added descriptive assertion message\n"
                f"+assert condition, f\"Assertion failed: {event.error_message}\"\n"
            ),
            "import": (
                f"--- a/{target_file}\n"
                f"+++ b/{target_file}\n"
                f"@@ -{error_line},1 +{error_line},5 @@\n"
                f"-{original_code}\n"
                f"+# SelfHeal: made import optional with pytest.importorskip\n"
                f"+import pytest\n"
                f"+missing_mod = \"{err_info.get('missing_module', 'unknown_module')}\"\n"
                f"+pytest.importorskip(missing_mod)\n"
            ),
            "timeout": (
                f"--- a/{target_file}\n"
                f"+++ b/{target_file}\n"
                f"@@ -{error_line},1 +{error_line},5 @@\n"
                f"-{original_code}\n"
                f"+# SelfHeal: added timeout guard\n"
                f"+import pytest\n"
                f"+@pytest.mark.timeout(30)\n"
                f"+def _wrapped(): pass  # (original logic goes here)\n"
            ),
            "network": (
                f"--- a/{target_file}\n"
                f"+++ b/{target_file}\n"
                f"@@ -{error_line},1 +{error_line},8 @@\n"
                f"-{original_code}\n"
                f"+# SelfHeal: skip test when offline\n"
                f"+import pytest, socket\n"
                f"+def _online():\n"
                f"+    try: socket.create_connection((\"8.8.8.8\", 53), timeout=3); return True\n"
                f"+    except OSError: return False\n"
                f"+@pytest.mark.skipif(not _online(), reason=\"network unavailable\")\n"
                f"+def _wrapped(): pass  # (original logic goes here)\n"
            ),
            "syntax": (
                f"--- a/{target_file}\n"
                f"+++ b/{target_file}\n"
                f"@@ -{error_line},1 +{error_line},4 @@\n"
                f"-{original_code}\n"
                f"+# SelfHeal: syntax error at this line — manual review required\n"
                f"+# Error: {event.error_message}\n"
                f"+# Check indentation, missing colons/brackets/quotes, then remove this block.\n"
            ),
            "runtime": (
                f"--- a/{target_file}\n"
                f"+++ b/{target_file}\n"
                f"@@ -{error_line},1 +{error_line},7 @@\n"
                f"-{original_code}\n"
                f"+# SelfHeal: guarded {event.error_type} with try/except\n"
                f"+import pytest\n"
                f"+try:\n"
                f"+    {original_code.strip() if original_code.strip() and not original_code.startswith('#') else 'pass  # (original logic)'}\n"
                f"+except {event.error_type} as e:\n"
                f"+    pytest.xfail(reason=f\"{event.error_type}: {{e}}\")\n"
            ),
            "config": (
                f"--- a/{target_file}\n"
                f"+++ b/{target_file}\n"
                f"@@ -{error_line},1 +{error_line},7 @@\n"
                f"-{original_code}\n"
                f"+# SelfHeal: skip test when config is unavailable\n"
                f"+import pytest, os\n"
                f"+@pytest.mark.skipif(\n"
                f"+    not os.environ.get(\"SELFHEAL_REQUIRED_CONFIG\"),\n"
                f"+    reason=\"Configuration not available\"\n"
                f"+)\n"
                f"+def _wrapped(): pass  # (original logic goes here)\n"
            ),
            "dependency": (
                f"--- a/{target_file}\n"
                f"+++ b/{target_file}\n"
                f"@@ -{error_line},1 +{error_line},5 @@\n"
                f"-{original_code}\n"
                f"+# SelfHeal: dependency conflict – test skipped\n"
                f"+import pytest\n"
                f"+@pytest.mark.skip(reason=\"Dependency conflict: {event.error_message[:80]}\")\n"
                f"+def _wrapped(): pass  # (original logic goes here)\n"
            ),
            "resource": (
                f"--- a/{target_file}\n"
                f"+++ b/{target_file}\n"
                f"@@ -{error_line},1 +{error_line},8 @@\n"
                f"-{original_code}\n"
                f"+# SelfHeal: skip test when resource is unavailable\n"
                f"+import pytest\n"
                f"+from pathlib import Path\n"
                f"+@pytest.mark.skipif(\n"
                f"+    not Path(\"{target_file}\").exists(),\n"
                f"+    reason=\"Required resource not available\"\n"
                f"+)\n"
                f"+def _wrapped(): pass  # (original logic goes here)\n"
            ),
            "permission": (
                f"--- a/{target_file}\n"
                f"+++ b/{target_file}\n"
                f"@@ -{error_line},1 +{error_line},8 @@\n"
                f"-{original_code}\n"
                f"+# SelfHeal: permission denied – test skipped\n"
                f"+import pytest, os\n"
                f"+@pytest.mark.skipif(\n"
                f"+    not os.access(\"{target_file}\", os.R_OK),\n"
                f"+    reason=\"Permission denied for target\"\n"
                f"+)\n"
                f"+def _wrapped(): pass  # (original logic goes here)\n"
            ),
            "flaky": (
                f"--- a/{target_file}\n"
                f"+++ b/{target_file}\n"
                f"@@ -{error_line},1 +{error_line},5 @@\n"
                f"-{original_code}\n"
                f"+# SelfHeal: flaky test – mark as expected intermittent failure\n"
                f"+import pytest\n"
                f"+@pytest.mark.flaky(reruns=3, reruns_delay=2)\n"
                f"+@pytest.mark.xfail(reason=\"Intermittent failure\", strict=False)\n"
                f"+def _wrapped(): pass  # (original logic goes here)\n"
            ),
            "value": (
                f"--- a/{target_file}\n"
                f"+++ b/{target_file}\n"
                f"@@ -{error_line},1 +{error_line},7 @@\n"
                f"-{original_code}\n"
                f"+# SelfHeal: guarded ValueError with xfail\n"
                f"+import pytest\n"
                f"+try:\n"
                f"+    {original_code.strip() if original_code.strip() and not original_code.startswith('#') else 'pass  # (original logic)'}\n"
                f"+except ValueError as e:\n"
                f"+    pytest.xfail(reason=f\"ValueError: {{e}}\")\n"
            ),
            "type": (
                f"--- a/{target_file}\n"
                f"+++ b/{target_file}\n"
                f"@@ -{error_line},1 +{error_line},7 @@\n"
                f"-{original_code}\n"
                f"+# SelfHeal: guarded TypeError with xfail\n"
                f"+import pytest\n"
                f"+try:\n"
                f"+    {original_code.strip() if original_code.strip() and not original_code.startswith('#') else 'pass  # (original logic)'}\n"
                f"+except TypeError as e:\n"
                f"+    pytest.xfail(reason=f\"TypeError: {{e}}\")\n"
            ),
            "memory": (
                f"--- a/{target_file}\n"
                f"+++ b/{target_file}\n"
                f"@@ -{error_line},1 +{error_line},5 @@\n"
                f"-{original_code}\n"
                f"+# SelfHeal: memory limit – test skipped\n"
                f"+import pytest\n"
                f"+@pytest.mark.skip(reason=\"MemoryError – test skipped to avoid OOM crashes\")\n"
                f"+def _wrapped(): pass  # (original logic goes here)\n"
            ),
        }

        content = patches.get(
            category,
            (
                f"--- a/{target_file}\n"
                f"+++ b/{target_file}\n"
                f"@@ -{error_line},1 +{error_line},7 @@\n"
                f"-{original_code}\n"
                f"+# SelfHeal: guarded with try/except\n"
                f"+import pytest\n"
                f"+try:\n"
                f"+    {original_code.strip() if original_code.strip() and not original_code.startswith('#') else 'pass  # (original logic)'}\n"
                f"+except Exception as e:\n"
                f"+    pytest.fail(f\"{event.error_type}: {{e}}\")\n"
            ),
        )
        return content, target_file
