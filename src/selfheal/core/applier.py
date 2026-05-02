"""Patch applier with backup, rollback, dry-run preview, and lifecycle management."""

import json
import logging
import re
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from selfheal.config import EngineConfig
from selfheal.events import PatchEvent

logger = logging.getLogger(__name__)

# Number of initial lines to inspect for unified-diff format detection
_DIFF_DETECT_LINES = 20

BACKUP_INDEX_FILE = ".selfheal/backup_index.json"


class PatchApplier:
    """Applies patches to source files with automatic backup and rollback.

    Features:
    - Automatic backup before patch application
    - Rollback on application failure
    - Dry-run preview (shows diff without modifying files)
    - Persistent backup index (survives process restarts)
    - Backup lifecycle management (cleanup old backups)
    """

    def __init__(self, config: EngineConfig):
        self.config = config
        self.backup_dir = Path(config.backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._applied_patches: dict[str, str] = {}  # patch_id -> backup_path
        self._load_backup_index()

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
                self._save_backup_index()
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
            # Remove stale index entry
            self._applied_patches.pop(patch.patch_id, None)
            self._save_backup_index()
            return False

        try:
            shutil.copy2(str(backup), target_file)
            patch.status = "rolled_back"
            self._applied_patches.pop(patch.patch_id, None)
            self._save_backup_index()
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
            for line in lines[:_DIFF_DETECT_LINES]
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

    # --- Dry-run preview ---

    def dry_run_preview(self, patch: PatchEvent) -> str:
        """Preview what a patch would change without modifying any files.

        Returns a human-readable summary of the changes.
        """
        target = patch.target_file
        if not target:
            return "[dry-run] No target file specified — nothing to preview."

        target_path = Path(target)
        if not target_path.exists():
            return f"[dry-run] Target file not found: {target}"

        content = patch.patch_content
        if self._is_unified_diff(content):
            return self._preview_diff(target_path, content)
        else:
            return self._preview_replacement(target_path, content)

    def _preview_diff(self, target_path: Path, diff_content: str) -> str:
        """Preview a unified diff: count additions and deletions."""
        lines = diff_content.splitlines()
        added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
        hunks = sum(1 for l in lines if l.startswith("@@"))

        summary = [
            f"[dry-run] Patch preview for: {target_path}",
            f"  +{added} lines added, -{removed} lines removed, {hunks} hunk(s)",
        ]
        # Show first 8 changed lines as preview
        changed = [l for l in lines if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))][:8]
        for line in changed:
            summary.append(f"  {line}")
        if len(changed) < added + removed:
            summary.append(f"  ... ({added + removed - len(changed)} more lines)")
        return "\n".join(summary)

    def _preview_replacement(self, target_path: Path, new_content: str) -> str:
        """Preview a full-file replacement."""
        original = target_path.read_text(encoding="utf-8")
        code = self._extract_code(new_content)
        orig_lines = len(original.splitlines())
        new_lines = len(code.splitlines())
        diff = new_lines - orig_lines
        return (
            f"[dry-run] Full replacement preview for: {target_path}\n"
            f"  Original: {orig_lines} lines → New: {new_lines} lines ({diff:+d})"
        )

    # --- Persistent backup index ---

    def _load_backup_index(self) -> None:
        """Load the persistent backup index from disk."""
        index_path = Path(BACKUP_INDEX_FILE)
        if index_path.exists():
            try:
                with open(index_path, encoding="utf-8") as f:
                    self._applied_patches = json.load(f)
                logger.debug(
                    "Loaded %d entries from backup index", len(self._applied_patches)
                )
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load backup index: %s", e)

    def _save_backup_index(self) -> None:
        """Persist the current backup index to disk."""
        try:
            index_path = Path(BACKUP_INDEX_FILE)
            index_path.parent.mkdir(parents=True, exist_ok=True)
            with open(index_path, "w", encoding="utf-8") as f:
                json.dump(self._applied_patches, f, indent=2)
        except OSError as e:
            logger.warning("Failed to save backup index: %s", e)

    def list_backups(self) -> dict[str, dict]:
        """List all tracked backups with metadata.

        Returns a dict mapping patch_id to {backup_path, target_file, exists}.
        """
        result = {}
        for patch_id, backup_path in self._applied_patches.items():
            bp = Path(backup_path)
            # Derive target file from backup name (strip timestamp + uuid)
            # Backup format: {filename}.{YYYYMMDD_HHMMSS}_{uuid8}.bak
            stem = bp.stem  # e.g., "test_utils.py.20260502_120000_a1b2c3d4"
            parts = stem.rsplit(".", 2)
            if len(parts) >= 3:
                target_name = ".".join(parts[:-1])  # preserves: "test_utils.py"
            else:
                target_name = stem
            # Reconstruct original path:
            # bp is .selfheal/backups/<file>.bak, so bp.parent.parent.parent is project root
            result[patch_id] = {
                "backup_path": backup_path,
                "target_file": str(bp.parent.parent.parent / target_name),
                "exists": bp.exists(),
                "size": bp.stat().st_size if bp.exists() else 0,
                "created": datetime.fromtimestamp(bp.stat().st_mtime).isoformat()
                if bp.exists() else "unknown",
            }
        return result

    # --- Backup lifecycle management ---

    def cleanup_backups(self, max_age_days: int = 30) -> dict:
        """Remove backup files older than max_age_days and orphan files.

        Returns a dict with cleanup statistics.
        """
        cutoff = datetime.now() - timedelta(days=max_age_days)
        stats = {"removed_index_entries": 0, "removed_orphan_files": 0, "errors": 0}

        # Remove expired entries from index
        expired_ids = []
        for patch_id, backup_path in list(self._applied_patches.items()):
            bp = Path(backup_path)
            if bp.exists():
                mtime = datetime.fromtimestamp(bp.stat().st_mtime)
                if mtime < cutoff:
                    try:
                        bp.unlink()
                        expired_ids.append(patch_id)
                        stats["removed_index_entries"] += 1
                    except OSError:
                        stats["errors"] += 1
            else:
                # Backup file missing — remove stale index entry
                expired_ids.append(patch_id)
                stats["removed_index_entries"] += 1

        for pid in expired_ids:
            del self._applied_patches[pid]

        # Remove orphan backup files (not in index)
        valid_paths = set(self._applied_patches.values())
        for f in self.backup_dir.glob("*.bak"):
            if str(f) not in valid_paths:
                try:
                    f.unlink()
                    stats["removed_orphan_files"] += 1
                except OSError:
                    stats["errors"] += 1

        self._save_backup_index()
        logger.info(
            "Backup cleanup: removed %d expired + %d orphans (%d errors)",
            stats["removed_index_entries"],
            stats["removed_orphan_files"],
            stats["errors"],
        )
        return stats
