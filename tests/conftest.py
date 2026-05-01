"""Shared fixtures and helpers for SelfHeal test suite."""

import os
import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from selfheal.config import Config, EngineConfig
from selfheal.engine import SelfHealEngine
from selfheal.events import (
    ClassificationEvent,
    ErrorSeverity,
    PatchEvent,
    TestFailureEvent,
    ValidationEvent,
)


# ---------------------------------------------------------------------------
# Event factory helpers
# ---------------------------------------------------------------------------

def make_failure(test_path="tests/test_calc.py::test_foo"):
    """Create a TestFailureEvent with sensible defaults."""
    return TestFailureEvent(
        test_path=test_path,
        error_type="AssertionError",
        error_message="assert 1 == 2",
        traceback="...",
    )


def make_classification(event=None, category="assertion", severity=None, confidence=0.8):
    """Create a ClassificationEvent with sensible defaults."""
    return ClassificationEvent(
        original_event=event or make_failure(),
        category=category,
        severity=severity or ErrorSeverity.MEDIUM,
        confidence=confidence,
    )


def make_patch(classification=None, patch_id=None, content="# fix",
               generator="template", target_file=None):
    """Create a PatchEvent with sensible defaults."""
    return PatchEvent(
        classification_event=classification or make_classification(),
        patch_id=patch_id or str(uuid.uuid4()),
        patch_content=content,
        generator=generator,
        target_file=target_file,
    )


def make_passed(patch, duration=0.5):
    """Create a 'passed' ValidationEvent."""
    return ValidationEvent(patch_event=patch, result="passed", duration=duration)


def make_failed(patch, error_message="test still fails", duration=0.3):
    """Create a 'failed' ValidationEvent."""
    return ValidationEvent(
        patch_event=patch, result="failed",
        error_message=error_message, duration=duration,
    )


# ---------------------------------------------------------------------------
# Mock engine helpers
# ---------------------------------------------------------------------------

INIT_PATCHES = [
    patch.object(SelfHealEngine, "_setup_components"),
    patch.object(SelfHealEngine, "_setup_watchers"),
    patch.object(SelfHealEngine, "_setup_reporters"),
    patch.object(SelfHealEngine, "_setup_plugin_watcher"),
]

# Backward-compatible alias (used by test_pipeline_e2e.py before migration)
_INIT_PATCHES = INIT_PATCHES


def create_mock_engine(config=None, extra_patches=None, keep_extra_patches=False,
                        **engine_kwargs):
    """Create engine with real pipeline stages, mocked component setup.

    Patches are started and stopped around construction so the engine
    object is left in a testable state without lingering mocks.

    If *keep_extra_patches* is True, only the base INIT_PATCHES are
    stopped – extra_patches remain active for the caller to manage.

    Additional keyword arguments are forwarded to ``SelfHealEngine()``
    (e.g. ``hooks=[...]``).
    """
    all_p = INIT_PATCHES + (extra_patches or [])
    for p in all_p:
        p.start()
    try:
        return SelfHealEngine(config or Config(), **engine_kwargs)
    finally:
        if keep_extra_patches:
            for p in INIT_PATCHES:
                p.stop()
        else:
            for p in all_p:
                p.stop()


def setup_mock_components(eng):
    """Set up mocked components: classifier, patcher, validator, reporter, store."""
    eng.classifier = MagicMock()
    eng.classifier.classify.return_value = make_classification()
    eng.patcher = MagicMock()
    eng.patcher.generate.return_value = make_patch()
    eng.validator = MagicMock()
    eng.validator.validate.return_value = make_passed(eng.patcher.generate.return_value)
    eng.reporter = MagicMock()
    eng.store = MagicMock()


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_dir():
    """Provide a temporary directory that is cleaned up after the test."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def temp_plugin_dir():
    """Create a temporary directory with a sample plugin file."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        (root / "hot_plugin.py").write_text("""\
from selfheal.interfaces.validator import ValidatorInterface
from selfheal.events import PatchEvent, ValidationEvent

class HotValidator(ValidatorInterface):
    name = "hot_validator"

    def validate(self, patch: PatchEvent) -> ValidationEvent:
        return ValidationEvent(patch_event=patch, result="passed")
""")
        yield root


@pytest.fixture
def sample_config():
    """Provide a default Config instance."""
    return Config()


@pytest.fixture
def mock_engine(sample_config):
    """Provide an engine with mocked component setup methods."""
    return create_mock_engine(sample_config)


# ---------------------------------------------------------------------------
# VCR (vcrpy) fixtures for LLM integration tests
# ---------------------------------------------------------------------------
# These fixtures use vcrpy to record and replay HTTP requests made to
# LLM APIs (OpenAI, Anthropic). Cassettes are stored in tests/vcr_cassettes/.
#
# To record new cassettes:
#   1. Set OPENAI_API_KEY (or ANTHROPIC_API_KEY) environment variable
#   2. Delete the relevant .yml file in vcr_cassettes/
#   3. Run the tests — cassettes will be auto-recorded
#
# In CI (without API keys), tests replay from cassettes.

VCR_CASSETTE_DIR = Path(__file__).parent / "vcr_cassettes"

# Sensitive headers to filter from cassettes
_SENSITIVE_HEADERS = [
    "authorization", "x-api-key", "api-key",
    "openai-organization", "anthropic-version",
]


def _vcr_config(**overrides):
    """Build a vcrpy config with sensible defaults for LLM API recording."""
    import vcr  # lazy import – only loaded when VCR tests are run

    def filter_request(request):
        """Strip sensitive headers from recorded requests."""
        for h in _SENSITIVE_HEADERS:
            if h in request.headers:
                request.headers[h] = "<FILTERED>"
        return request

    defaults = dict(
        cassette_library_dir=str(VCR_CASSETTE_DIR),
        record_mode="none" if os.environ.get("CI") else "once",
        match_on=["method", "scheme", "host", "port", "path", "query"],
        filter_headers=[*_SENSITIVE_HEADERS],
        before_record_request=filter_request,
    )
    defaults.update(overrides)
    return vcr.VCR(**defaults)


@pytest.fixture
def vcr_openai():
    """VCR fixture for OpenAI API requests.

    Uses tests/vcr_cassettes/openai/ cassette directory.
    In CI (CI=1), replays only; locally, records once then replays.
    """
    import vcr
    return _vcr_config(
        cassette_library_dir=str(VCR_CASSETTE_DIR / "openai"),
    )


@pytest.fixture
def vcr_anthropic():
    """VCR fixture for Anthropic API requests."""
    import vcr
    return _vcr_config(
        cassette_library_dir=str(VCR_CASSETTE_DIR / "anthropic"),
    )
