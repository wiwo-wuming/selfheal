"""Tests for PatchApplier."""

import tempfile
import uuid
from pathlib import Path

import pytest

from selfheal.config import EngineConfig
from selfheal.core.applier import PatchApplier
from selfheal.events import (
    ClassificationEvent,
    ErrorSeverity,
    PatchEvent,
    TestFailureEvent,
)


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def applier(temp_dir):
    cfg = EngineConfig(backup_dir=str(temp_dir / "backups"))
    return PatchApplier(cfg)


@pytest.fixture
def target_file(temp_dir):
    path = temp_dir / "target.py"
    path.write_text("print('hello')\n")
    return path


def make_classification(test_path="tests/test_foo.py"):
    original = TestFailureEvent(
        test_path=test_path,
        error_type="AssertionError",
        error_message="assert 1 == 2",
        traceback="...",
    )
    return ClassificationEvent(
        original_event=original,
        category="assertion",
        severity=ErrorSeverity.MEDIUM,
        confidence=0.8,
    )


def make_patch(classification, target_file, content="print('fixed')\n"):
    return PatchEvent(
        classification_event=classification,
        patch_id=str(uuid.uuid4()),
        patch_content=content,
        generator="template",
        target_file=str(target_file),
    )


class TestPatchApplier:
    def test_apply_replacement(self, applier, target_file):
        """Test applying a full-file replacement patch."""
        classification = make_classification()
        patch = make_patch(classification, target_file, "print('fixed')\n")

        assert applier.apply(patch) is True
        assert patch.status == "applied"
        assert patch.applied_at is not None
        assert target_file.read_text() == "print('fixed')\n"

    def test_backup_created(self, applier, target_file):
        """Test that a backup is created before applying."""
        original = target_file.read_text()
        classification = make_classification()
        patch = make_patch(classification, target_file, "print('new')\n")

        applier.apply(patch)
        assert patch.backup_path is not None
        backup = Path(patch.backup_path)
        assert backup.exists()
        assert backup.read_text() == original

    def test_rollback(self, applier, target_file):
        """Test rolling back a patch."""
        original = target_file.read_text()
        classification = make_classification()
        patch = make_patch(classification, target_file, "modified\n")

        applier.apply(patch)
        assert target_file.read_text() == "modified\n"

        assert applier.rollback(patch) is True
        assert patch.status == "rolled_back"
        assert target_file.read_text() == original

    def test_apply_no_target(self, applier):
        """Test that apply fails without target_file."""
        classification = make_classification()
        patch = PatchEvent(
            classification_event=classification,
            patch_id=str(uuid.uuid4()),
            patch_content="code",
            generator="template",
            target_file=None,
        )
        assert applier.apply(patch) is False

    def test_apply_missing_file(self, applier, temp_dir):
        """Test that apply fails for non-existent target."""
        classification = make_classification()
        patch = make_patch(classification, temp_dir / "nonexistent.py", "code")

        assert applier.apply(patch) is False

    def test_apply_empty_patch(self, applier, target_file):
        """Test that empty patch content is rejected."""
        classification = make_classification()
        patch = make_patch(classification, target_file, "")

        assert applier.apply(patch) is False

    def test_extract_code_from_markdown(self, applier, target_file):
        """Test extracting code from markdown fenced blocks."""
        classification = make_classification()
        patch = make_patch(
            classification,
            target_file,
            "```python\nprint('extracted')\n```\n",
        )

        assert applier.apply(patch) is True
        assert "print('extracted')" in target_file.read_text()
        assert "```" not in target_file.read_text()

    def test_is_unified_diff(self, applier):
        """Test unified diff detection."""
        diff = """--- a/file.py
+++ b/file.py
@@ -1,3 +1,3 @@
-old
+new"""
        assert PatchApplier._is_unified_diff(diff) is True
        assert PatchApplier._is_unified_diff("just code") is False

    def test_get_backup_path(self, applier, target_file):
        """Test retrieving backup path by patch ID."""
        classification = make_classification()
        patch = make_patch(classification, target_file, "new")

        applier.apply(patch)
        backup = applier.get_backup_path(patch.patch_id)
        assert backup is not None
        assert Path(backup).exists()
