"""Shared unified-diff parsing and application utilities.

Extracted from applier.py and docker_validator.py to eliminate ~50 lines
of duplicated hunk-parsing code.
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

# Number of initial lines to inspect for unified-diff format detection
DIFF_DETECT_LINES = 20


def is_unified_diff(content: str) -> bool:
    """Check if content looks like a unified diff format."""
    lines = content.strip().split("\n")
    return any(
        line.startswith(("--- ", "+++ ", "@@ ", "diff --git"))
        for line in lines[:DIFF_DETECT_LINES]
    )


def parse_and_apply_diff(
    original_lines: list[str], diff_content: str
) -> Optional[list[str]]:
    """Parse a unified diff and apply to original lines.

    Returns the patched lines on success, or None if the diff content
    contains no parseable hunks (malformed or unrecognised input).
    """
    diff_lines = diff_content.splitlines(keepends=True)

    hunk_header_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

    result = list(original_lines)  # copy
    offset = 0  # track cumulative line offset from previous hunks
    hunks_applied = 0

    i = 0
    while i < len(diff_lines):
        line = diff_lines[i]

        # Skip diff header lines
        if line.startswith(("---", "+++", "diff ")):
            i += 1
            continue

        # Parse hunk header
        match = hunk_header_re.match(line)
        if not match:
            i += 1
            continue

        old_start = int(match.group(1))
        old_count = int(match.group(2)) if match.group(2) is not None else 1

        i += 1  # move past hunk header
        remove_count = 0
        add_count = 0
        additions: list[str] = []
        line_idx = 0

        # Process lines in this hunk
        while i < len(diff_lines) and line_idx < old_count + 100:
            if i >= len(diff_lines):
                break
            hline = diff_lines[i]

            # Check if we've reached the next hunk or diff header
            if hline.startswith("@@") or hline.startswith("diff ") or hline.startswith("--- "):
                break

            if hline.startswith("+"):
                # Addition line
                additions.append(hline[1:])
                add_count += 1
            elif hline.startswith("-"):
                # Removal line (skip from original)
                remove_count += 1
            elif hline.startswith(" "):
                # Context line
                additions.append(hline[1:])
            elif hline.startswith("\\"):
                # "\ No newline at end of file" — skip
                pass
            else:
                # Treat as context if no prefix
                additions.append(hline)

            i += 1
            line_idx += 1

        # Apply this hunk: replace old_start-1..old_start+old_count-1 with additions
        insert_pos = old_start - 1 + offset
        result[insert_pos:insert_pos + old_count] = additions
        offset += len(additions) - old_count
        hunks_applied += 1

    if hunks_applied == 0:
        return None

    return result


def apply_patch_to_file(target: Path, patch_content: str) -> bool:
    """Apply a patch to a file on disk.

    Auto-detects unified-diff vs full replacement content.
    For unified diffs, tries built-in parsing first; falls back to the
    system ``patch`` command when built-in parsing cannot handle the diff.
    For non-diff content, writes as a full file replacement.

    Returns True on success, False on failure.
    """
    if not target.exists():
        return False

    if is_unified_diff(patch_content):
        original = target.read_text(encoding="utf-8")
        original_lines = original.splitlines(keepends=True)

        patched_lines = parse_and_apply_diff(original_lines, patch_content)

        if patched_lines is not None:
            target.write_text("".join(patched_lines), encoding="utf-8")
            return True

        # Fallback to system patch command
        return _system_patch(target, patch_content)
    else:
        # Full replacement (not a unified diff)
        target.write_text(patch_content, encoding="utf-8")
        return True


def _system_patch(target: Path, patch_content: str) -> bool:
    """Apply a unified diff using the system ``patch`` command as fallback."""
    if sys.platform == "win32":
        return False
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".patch", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(patch_content)
            tmp_path = tmp.name

        result = subprocess.run(
            ["patch", "-p0", str(target), "-i", tmp_path],
            capture_output=True,
            timeout=30,
        )
        Path(tmp_path).unlink(missing_ok=True)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
