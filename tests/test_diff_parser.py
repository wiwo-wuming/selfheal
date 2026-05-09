"""Tests for diff_parser — shared unified-diff utilities."""

import subprocess
from unittest.mock import patch

import pytest

from selfheal.core.diff_parser import (
    apply_patch_to_file,
    is_unified_diff,
    parse_and_apply_diff,
)


class TestIsUnifiedDiff:
    def test_positive(self):
        """Content with --- a/file header is recognised as a unified diff."""
        content = """--- a/file.py
+++ b/file.py
@@ -1,3 +1,3 @@
-old
+new"""
        assert is_unified_diff(content) is True

    def test_negative(self):
        """Plain source code is not mistaken for a unified diff."""
        content = """def hello():
    print("hello world")
    return 42"""
        assert is_unified_diff(content) is False

    def test_diff_git_header(self):
        """git-format diff header is recognised."""
        content = """diff --git a/x.py b/x.py
--- a/x.py
+++ b/x.py
@@ -1 +1 @@
-foo
+bar"""
        assert is_unified_diff(content) is True


class TestParseAndApplyDiff:
    def test_add_lines(self):
        """Adding new lines via a single hunk."""
        original = ["line1\n", "line2\n", "line3\n"]
        diff = """--- a/f.py
+++ b/f.py
@@ -1,3 +1,4 @@
 line1
 line2
+inserted
 line3
"""
        result = parse_and_apply_diff(original, diff)
        assert result == ["line1\n", "line2\n", "inserted\n", "line3\n"]

    def test_remove_lines(self):
        """Removing lines via a single hunk."""
        original = ["line1\n", "line2\n", "line3\n"]
        diff = """--- a/f.py
+++ b/f.py
@@ -1,3 +1,2 @@
 line1
-line2
 line3
"""
        result = parse_and_apply_diff(original, diff)
        assert result == ["line1\n", "line3\n"]

    def test_modify_lines(self):
        """Modifying an existing line."""
        original = ["first\n", "second\n", "third\n"]
        diff = """--- a/f.py
+++ b/f.py
@@ -1,3 +1,3 @@
 first
-second
+modified
 third
"""
        result = parse_and_apply_diff(original, diff)
        assert result == ["first\n", "modified\n", "third\n"]

    def test_invalid_diff(self):
        """Garbage input with no parseable hunks returns None."""
        original = ["line1\n"]
        diff = "this is not a diff at all"
        result = parse_and_apply_diff(original, diff)
        assert result is None


class TestApplyPatchToFile:
    def test_replacement_non_diff(self, tmp_path):
        """Non-diff content is written as full replacement."""
        target = tmp_path / "f.py"
        target.write_text("original\n")
        assert apply_patch_to_file(target, "replaced\n") is True
        assert target.read_text() == "replaced\n"

    def test_unified_diff_on_disk(self, tmp_path):
        """Unified-diff is applied to an existing file."""
        target = tmp_path / "f.py"
        target.write_text("line1\nline2\nline3\n")
        diff = """--- a/f.py
+++ b/f.py
@@ -1,3 +1,4 @@
 line1
+inserted
 line2
 line3
"""
        assert apply_patch_to_file(target, diff) is True
        assert target.read_text() == "line1\ninserted\nline2\nline3\n"

    def test_missing_target(self, tmp_path):
        """apply_patch_to_file returns False when the target does not exist."""
        target = tmp_path / "missing.py"
        assert apply_patch_to_file(target, "content") is False


class TestSystemPatch:
    def test_system_patch_skipped_on_windows(self, tmp_path):
        from selfheal.core.diff_parser import _system_patch
        target = tmp_path / "test.py"
        target.write_text("content")
        with patch("selfheal.core.diff_parser.sys.platform", "win32"):
            with patch("subprocess.run") as mock_run:
                result = _system_patch(target, "fake diff")
                assert result is False
                mock_run.assert_not_called()

    def test_system_patch_unix_success(self, tmp_path):
        from selfheal.core.diff_parser import _system_patch
        target = tmp_path / "test.py"
        target.write_text("original")
        with patch("selfheal.core.diff_parser.sys.platform", "linux"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                result = _system_patch(target, "fake diff")
                assert result is True
                mock_run.assert_called_once()

    def test_system_patch_unix_failure(self, tmp_path):
        from selfheal.core.diff_parser import _system_patch
        target = tmp_path / "test.py"
        target.write_text("original")
        with patch("selfheal.core.diff_parser.sys.platform", "linux"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 1
                result = _system_patch(target, "fake diff")
                assert result is False
