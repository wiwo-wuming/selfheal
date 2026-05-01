"""Patch applier with backup and rollback support."""

import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from selfheal.config import EngineConfig
from selfheal.events import PatchEvent

logger = logging.getLogger(__name__)


class PatchApplier:
    """Applies patches to source files with automatic backup and rollback."""

    def __init__(self, config: EngineConfig):
        self.config = config
        self.backup_dir = Path(config.backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._applied_patches: dict[str, str] = {}  # patch_id -> backup_path

    def apply(self, patch: PatchEvent) -> bool:
        """Apply a patch to the target file.

        Returns True on success, False on failure.
        Creates a backup before modifying the file.
        """
        target = patch.target_file
        if not target:
            logger.error("Patch has no target_file, cannot apply")
            return False

        target_path = Path(target)
        if not target_path.exists():
            logger.error(f"Target file not found: {target_path}")
            return False

        try:
            # Create backup
            backup_path = self._backup_file(target_path)
            patch.backup_path = str(backup_path)

            # Determine patch strategy
            content = patch.patch_content

            if self._is_unified_diff(content):
                success = self._apply_diff(target_path, content)
            else:
                success = self._apply_replacement(target_path, content)

            if success:
                patch.status = "applied"
                patch.applied_at = datetime.now()
                self._applied_patches[patch.patch_id] = str(backup_path)
                logger.info(f"Patch {patch.patch_id} applied to {target_path}")
            else:
                self._rollback(target_path, backup_path)
                patch.status = "rejected"
                logger.warning(f"Patch {patch.patch_id} failed to apply, rolled back")

            return success

        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.error(f"Error applying patch {patch.patch_id}: {e}")
            # Attempt rollback
            if backup_path := patch.backup_path:
                self._rollback(target_path, Path(backup_path))
            patch.status = "rejected"
            return False

    def rollback(self, patch: PatchEvent) -> bool:
        """Rollback a previously applied patch."""
        if not patch.backup_path:
            logger.warning(f"No backup for patch {patch.patch_id}")
            return False

        target_file = patch.target_file
        if not target_file:
            return False

        backup = Path(patch.backup_path)
        if not backup.exists():
            logger.error(f"Backup file not found: {backup}")
            return False

        try:
            shutil.copy2(str(backup), target_file)
            patch.status = "rolled_back"
            logger.info(f"Rolled back patch {patch.patch_id}")
            return True
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.error(f"Rollback failed for {patch.patch_id}: {e}")
            return False

    def _backup_file(self, file_path: Path) -> Path:
        """Create a timestamped backup of a file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{file_path.name}.{timestamp}_{uuid.uuid4().hex[:8]}.bak"
        backup_path = self.backup_dir / backup_name
        shutil.copy2(str(file_path), str(backup_path))
        logger.debug(f"Backed up {file_path} -> {backup_path}")
        return backup_path

    def _rollback(self, target: Path, backup: Path) -> None:
        """Restore file from backup."""
        if backup.exists():
            shutil.copy2(str(backup), str(target))
            logger.info(f"Rolled back {target} from {backup}")

    @staticmethod
    def _is_unified_diff(content: str) -> bool:
        """Check if content looks like a unified diff."""
        lines = content.strip().split("\n")
        return any(
            line.startswith(("--- ", "+++ ", "@@ ", "diff --git"))
            for line in lines[:20]
        )

    def _apply_diff(self, target_path: Path, diff_content: str) -> bool:
        """Apply a unified diff patch."""
        try:
            original = target_path.read_text(encoding="utf-8")
            original_lines = original.splitlines(keepends=True)

            # Parse the unified diff into hunks and apply
            patched_lines = self._apply_unified_diff(original_lines, diff_content)

            if patched_lines is None:
                logger.warning("Failed to parse/apply unified diff, falling back to subprocess patch")
                return self._apply_diff_subprocess(target_path, diff_content)

            target_path.write_text("".join(patched_lines), encoding="utf-8")
            return True
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"Diff application failed: {e}")
            return False

    def _apply_unified_diff(
        self, original_lines: list[str], diff_content: str
    ) -> Optional[list[str]]:
        """Parse and apply a unified diff manually.

        Returns the patched lines, or None if parsing fails.
        """
        diff_lines = diff_content.splitlines(keepends=True)

        # Find hunks starting with @@ -a,b +c,d @@
        import re
        hunk_header_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

        result = list(original_lines)  # copy
        offset = 0  # track cumulative line offset from previous hunks

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
            additions = []
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

        return result

    def _apply_diff_subprocess(self, target_path: Path, diff_content: str) -> bool:
        """Apply a unified diff using the system 'patch' command as fallback."""
        import subprocess
        import tempfile

        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".patch", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(diff_content)
                tmp_path = tmp.name

            result = subprocess.run(
                ["patch", "-p0", "--no-backup-if-mismatch", str(target_path), "-i", tmp_path],
                capture_output=True,
                text=True,
                timeout=30,
            )

            # Clean up temp file
            Path(tmp_path).unlink(missing_ok=True)

            if result.returncode == 0:
                return True
            else:
                logger.warning(f"patch command failed: {result.stderr}")
                return False
        except FileNotFoundError:
            logger.warning("'patch' command not available on this system")
            return False
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"subprocess patch failed: {e}")
            return False

    def _apply_replacement(self, target_path: Path, new_content: str) -> bool:
        """Apply a full-file replacement patch."""
        try:
            # Strip markdown code fences if present
            code = self._extract_code(new_content)

            if not code.strip():
                logger.warning("Empty patch content after extraction")
                return False

            target_path.write_text(code, encoding="utf-8")
            return True
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.error(f"Replacement application failed: {e}")
            return False

    @staticmethod
    def _extract_code(content: str) -> str:
        """Extract code from patch content, removing markdown fences."""
        import re

        # Try to extract from code blocks
        code_blocks = re.findall(r"```(?:python|py|diff)?\n(.*?)```", content, re.DOTALL)
        if code_blocks:
            # Return the largest block
            return max(code_blocks, key=len).strip() + "\n"

        # Remove common comment markers from non-code lines
        lines = content.split("\n")
        code_lines = []
        for line in lines:
            # Skip lines that are purely comments/instructions
            if line.strip().startswith(("# Fix for", "# Generated by", "# SelfHeal")):
                continue
            code_lines.append(line)

        return "\n".join(code_lines)

    def get_backup_path(self, patch_id: str) -> Optional[str]:
        """Get the backup path for a patch."""
        return self._applied_patches.get(patch_id)
