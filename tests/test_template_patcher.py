"""Tests for TemplatePatcher, traceback parsing, and template rendering."""

from unittest.mock import MagicMock, patch

import pytest

from selfheal.config import PatcherConfig
from selfheal.core.patchers.template_patcher import (
    TemplatePatcher,
    _parse_traceback,
    _parse_error_message,
)
from selfheal.events import (
    TestFailureEvent,
    ClassificationEvent,
    ErrorSeverity,
    PatchEvent,
)


# ------------------------------------------------------------------
# Reset global experience store before each test to avoid cross-test pollution
# ------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_experience():
    """Reset the global experience store before each test."""
    from selfheal.core.experience import reset_experience, get_experience
    import sqlite3

    # Close existing singleton if any
    reset_experience()

    # Open fresh store and clear all data
    try:
        exp = get_experience()
        exp._get_conn().execute("DELETE FROM experiences")
        exp._get_conn().commit()
        exp.close()
    except Exception:
        pass

    reset_experience()
    yield

    try:
        exp2 = get_experience()
        exp2.close()
    except Exception:
        pass
    reset_experience()


# ------------------------------------------------------------------
# Traceback parsing tests
# ------------------------------------------------------------------

REAL_TRACEBACK = """\
Traceback (most recent call last):
  File "C:/project/tests/test_math.py", line 42, in test_add
    assert add(1, 2) == 5
  File "C:/project/src/math_utils.py", line 10, in add
    return a + b
AssertionError: assert 3 == 5
"""


class TestParseTraceback:
    """Test _parse_traceback() helper."""

    def test_extracts_file_and_line(self):
        info = _parse_traceback(REAL_TRACEBACK)
        # Last File line in traceback points to the error origin (source code)
        assert "math_utils.py" in info["error_file"]
        assert info["error_line"] == 10  # last File line
        assert info["error_func"] == "add"

    def test_extracts_original_code(self):
        info = _parse_traceback(REAL_TRACEBACK)
        assert "return a + b" in info["original_code"]

    def test_empty_traceback(self):
        info = _parse_traceback("")
        assert info["error_file"] == ""
        assert info["error_line"] is None
        assert info["error_func"] is None

    def test_none_traceback_should_be_handled(self):
        """_parse_traceback receives str, but if empty string is passed it's fine."""
        info = _parse_traceback("")
        assert info["error_line"] is None

    def test_minimal_traceback(self):
        tb = 'File "/x.py", line 5, in foo\n    bar()\nSomeError: msg'
        info = _parse_traceback(tb)
        assert info["error_file"] == "/x.py"
        assert info["error_line"] == 5
        assert info["error_func"] == "foo"
        assert "bar()" in info["original_code"]

    def test_traceback_without_function_name(self):
        # Some tracebacks omit ", in funcname"
        tb = 'File "/x.py", line 99\n    code\nError: boom'
        info = _parse_traceback(tb)
        assert info["error_file"] == "/x.py"
        assert info["error_line"] == 99
        assert info["error_func"] is None


class TestParseErrorMessage:
    """Test _parse_error_message() helper."""

    def test_assertion_extracts_expected_actual(self):
        info = _parse_error_message("assert 3 == 5", "AssertionError")
        assert info["expected"] == "5"
        assert info["actual"] == "3"

    def test_assertion_without_compare(self):
        info = _parse_error_message("assert False", "AssertionError")
        assert info["expected"] is None
        assert info["actual"] is None
        assert info["error_detail"] == "assert False"

    def test_import_extracts_module_name(self):
        info = _parse_error_message(
            "No module named 'numpy'", "ModuleNotFoundError"
        )
        assert info["missing_module"] == "numpy"

    def test_import_extracts_cannot_import_name(self):
        info = _parse_error_message(
            "cannot import name 'helpers' from 'utils'",
            "ImportError",
        )
        assert info["missing_module"] == "helpers"

    def test_other_error_no_extraction(self):
        info = _parse_error_message(
            "unsupported operand type(s) for +: 'int' and 'str'",
            "TypeError",
        )
        assert info["missing_module"] is None
        assert info["expected"] is None
        assert info["actual"] is None
        assert "unsupported" in info["error_detail"]

    def test_empty_error_message(self):
        info = _parse_error_message("", "RuntimeError")
        assert info["error_detail"] == ""
        assert info["missing_module"] is None


# ------------------------------------------------------------------
# TemplatePatcher tests
# ------------------------------------------------------------------


class TestTemplatePatcherGenerate:
    """Test that TemplatePatcher.generate() produces valid unified diffs."""

    @pytest.fixture
    def patcher(self):
        return TemplatePatcher(PatcherConfig(templates_dir="patches/"))

    @pytest.fixture
    def assertion_event(self):
        failure = TestFailureEvent(
            test_path="tests/test_foo.py",
            error_type="AssertionError",
            error_message="assert 3 == 5",
            traceback=REAL_TRACEBACK,
        )
        return ClassificationEvent(
            original_event=failure,
            category="assertion",
            severity=ErrorSeverity.MEDIUM,
            confidence=0.9,
            reasoning="Matched pattern: AssertionError",
        )

    @pytest.fixture
    def import_event(self):
        failure = TestFailureEvent(
            test_path="tests/test_bar.py",
            error_type="ModuleNotFoundError",
            error_message="No module named 'numpy'",
            traceback='Traceback (most recent call last):\n  File "tests/test_bar.py", line 3, in <module>\n    import numpy\nModuleNotFoundError: No module named \'numpy\'\n',
        )
        return ClassificationEvent(
            original_event=failure,
            category="import",
            severity=ErrorSeverity.HIGH,
            confidence=0.95,
            reasoning="Matched pattern: ModuleNotFoundError",
        )

    def test_generates_unified_diff_format(self, patcher, assertion_event):
        patch = patcher.generate(assertion_event)
        content = patch.patch_content
        lines = content.strip().split("\n")
        assert lines[0].startswith("--- a/"), f"Expected diff header, got: {lines[0]}"
        assert lines[1].startswith("+++ b/")
        assert any(l.startswith("@@") for l in lines[:10])

    def test_sets_target_file_from_traceback(self, patcher, assertion_event):
        patch = patcher.generate(assertion_event)
        assert patch.target_file is not None
        # Target should be the error origin file from traceback (source, not test)
        assert "test_math.py" in patch.target_file or "math_utils.py" in patch.target_file

    def test_sets_generator_to_template(self, patcher, assertion_event):
        patch = patcher.generate(assertion_event)
        assert patch.generator == "template"

    def test_patch_id_is_unique(self, patcher, assertion_event):
        patch1 = patcher.generate(assertion_event)
        patch2 = patcher.generate(assertion_event)
        assert patch1.patch_id != patch2.patch_id

    def test_import_template_sets_target_file(self, patcher, import_event):
        patch = patcher.generate(import_event)
        assert patch.target_file is not None

    def test_import_template_includes_importorskip(self, patcher, import_event):
        patch = patcher.generate(import_event)
        assert "importorskip" in patch.patch_content

    def test_fallback_for_unknown_category(self):
        """Categories without a template fall back to _generic.py.j2."""
        patcher = TemplatePatcher(PatcherConfig(templates_dir="patches/"))
        failure = TestFailureEvent(
            test_path="tests/test_x.py",
            error_type="CustomError",
            error_message="bad",
        )
        classification = ClassificationEvent(
            original_event=failure,
            category="unknown",
            severity=ErrorSeverity.MEDIUM,
            confidence=0.1,
        )
        patch = patcher.generate(classification)
        assert patch.generator == "template"
        assert "--- a/" in patch.patch_content
        assert "+++ b/" in patch.patch_content

    def test_fallback_with_no_traceback_uses_test_path(self):
        """When traceback is empty, target_file falls back to test_path."""
        patcher = TemplatePatcher(PatcherConfig(templates_dir="patches/"))
        failure = TestFailureEvent(
            test_path="tests/test_nopath.py",
            error_type="RuntimeError",
            error_message="oops",
        )
        classification = ClassificationEvent(
            original_event=failure,
            category="runtime",
            severity=ErrorSeverity.MEDIUM,
            confidence=0.5,
        )
        patch = patcher.generate(classification)
        assert patch.target_file == "tests/test_nopath.py"

    def test_all_categories_produce_valid_diff(self):
        """Every registered category template should render a valid unified diff."""
        patcher = TemplatePatcher(PatcherConfig(templates_dir="patches/"))
        categories = [
            ("assertion", "AssertionError", "assert 1 == 2"),
            ("import", "ModuleNotFoundError", "No module named 'foo'"),
            ("timeout", "TimeoutError", "timed out"),
            ("runtime", "TypeError", "bad type"),
            ("syntax", "SyntaxError", "invalid syntax"),
            ("network", "ConnectionError", "refused"),
        ]
        for cat, etype, emsg in categories:
            failure = TestFailureEvent(
                test_path=f"tests/test_{cat}.py",
                error_type=etype,
                error_message=emsg,
            )
            classification = ClassificationEvent(
                original_event=failure,
                category=cat,
                severity=ErrorSeverity.MEDIUM,
                confidence=0.8,
            )
            patch = patcher.generate(classification)
            assert "--- a/" in patch.patch_content, f"Category '{cat}' did not produce diff header"
            assert "+++ b/" in patch.patch_content, f"Category '{cat}' did not produce diff header"
            assert "@@" in patch.patch_content, f"Category '{cat}' did not produce hunk header"

    def test_build_template_context_has_all_keys(self):
        """_build_template_context should return at least the documented keys."""
        patcher = TemplatePatcher(PatcherConfig(templates_dir="patches/"))
        failure = TestFailureEvent(
            test_path="tests/test_x.py",
            error_type="ValueError",
            error_message="bad value",
            traceback=REAL_TRACEBACK,
        )
        classification = ClassificationEvent(
            original_event=failure,
            category="runtime",
            severity=ErrorSeverity.HIGH,
            confidence=0.7,
            reasoning="Match",
        )
        ctx = patcher._build_template_context(classification)

        required_keys = [
            "event", "classification", "category", "severity",
            "confidence", "reasoning", "error_type", "error_message",
            "test_path", "target_file", "error_line", "error_func",
            "original_code", "expected", "actual", "missing_module",
            "error_detail",
        ]
        for key in required_keys:
            assert key in ctx, f"Missing key: {key}"
