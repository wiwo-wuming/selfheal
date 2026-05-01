"""Tests for PluginLoader — loading, tracking, and hot-reloading."""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from selfheal.plugins.loader import PluginLoader, _get_interface_map
from selfheal.registry import get_registry


@pytest.fixture
def fresh_registry():
    """Return a clean Registry for isolated tests."""
    from selfheal.registry import Registry
    return Registry()


@pytest.fixture
def loader(fresh_registry):
    """Return a PluginLoader backed by a fresh registry."""
    loader = PluginLoader()
    loader.registry = fresh_registry
    return loader


@pytest.fixture
def plugin_dir():
    """Create a temporary directory with plugin .py files."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()

        # Plugin 1: a custom validator
        (root / "my_validator.py").write_text("""\
from selfheal.interfaces.validator import ValidatorInterface
from selfheal.events import PatchEvent, ValidationEvent

class MyValidator(ValidatorInterface):
    name = "my_validator"

    def validate(self, patch: PatchEvent) -> ValidationEvent:
        return ValidationEvent(patch_event=patch, result="passed")
""")

        # Plugin 2: a custom reporter
        (root / "my_reporter.py").write_text("""\
from selfheal.interfaces.reporter import ReporterInterface

class MyReporter(ReporterInterface):
    name = "my_reporter"

    def report(self, event):
        print(f"Reporting: {event}")
""")

        # Plugin 3: a module with multiple components
        (root / "multi_plugin.py").write_text("""\
from selfheal.interfaces.classifier import ClassifierInterface
from selfheal.interfaces.patcher import PatcherInterface

class MyClassifier(ClassifierInterface):
    name = "my_classifier"

    def classify(self, event):
        from selfheal.events import ClassificationEvent, ErrorSeverity
        return ClassificationEvent(
            original_event=event, category="unknown",
            severity=ErrorSeverity.MEDIUM, confidence=0.5,
        )

class MyPatcher(PatcherInterface):
    name = "my_patcher"

    def generate(self, classification):
        from selfheal.events import PatchEvent
        return PatchEvent(
            classification_event=classification,
            patch_id="mp-1", patch_content="# patch",
            generator="my_patcher",
        )
""")

        yield root


class TestPluginLoaderLoad:
    """Tests for PluginLoader loading."""

    def test_load_from_path_registers_components(self, loader, plugin_dir):
        """Loading from a path should register discovered components."""
        loader.load_from_path(plugin_dir)

        assert loader.registry.get_validator("my_validator") is not None
        assert loader.registry.get_reporter("my_reporter") is not None
        assert loader.registry.get_classifier("my_classifier") is not None
        assert loader.registry.get_patcher("my_patcher") is not None

    def test_load_from_path_tracks_plugins(self, loader, plugin_dir):
        """Loaded plugin classes should be tracked."""
        loader.load_from_path(plugin_dir)

        plugins = loader.get_loaded_plugins()
        assert len(plugins) >= 4  # one per class

        # All should be types (classes)
        assert all(isinstance(p, type) for p in plugins)

    def test_load_from_path_tracks_modules(self, loader, plugin_dir):
        """Loaded modules should be tracked for hot-reloading."""
        loader.load_from_path(plugin_dir)

        modules = loader.get_loaded_modules()
        assert "my_validator" in modules
        assert "my_reporter" in modules
        assert "multi_plugin" in modules

        # Should be actual module objects
        import types
        assert isinstance(modules["my_validator"], types.ModuleType)

    def test_load_from_nonexistent_path(self, loader):
        """Loading from a nonexistent path should warn, not crash."""
        loader.load_from_path(Path("/nonexistent/path/for/tests"))
        assert loader.get_loaded_plugins() == []

    def test_load_empty_directory(self, loader):
        """Loading from an empty directory should work fine."""
        with tempfile.TemporaryDirectory() as tmp:
            loader.load_from_path(Path(tmp))
        # No plugins loaded, but no error either
        assert loader.get_loaded_plugins() == []

    def test_load_skips_private_modules(self, loader, plugin_dir):
        """Modules starting with _ should be skipped."""
        (plugin_dir / "_private.py").write_text("""\
class PrivateComponent:
    name = "private"
""")
        loader.load_from_path(plugin_dir)

        modules = loader.get_loaded_modules()
        assert "_private" not in modules


class TestPluginLoaderHotReload:
    """Tests for PluginLoader hot-reloading."""

    def test_reload_module_updates_registry(self, loader, plugin_dir):
        """Reloading a module should re-register its components."""
        loader.load_from_path(plugin_dir)

        # Verify original class is registered
        original_cls = loader.registry.get_validator("my_validator")
        assert original_cls is not None

        # Modify the plugin file — add a comment to change the class object
        validator_file = plugin_dir / "my_validator.py"
        original_content = validator_file.read_text()
        new_content = original_content + "\n# Hot-reloaded: v2\n"
        validator_file.write_text(new_content)

        # Reload
        success = loader.reload_module("my_validator")
        assert success

        # Registry should now point to the reloaded class
        reloaded_cls = loader.registry.get_validator("my_validator")
        assert reloaded_cls is not None
        # After reload, it's a new class object (different id)
        assert reloaded_cls is not original_cls

    def test_reload_unknown_module_returns_false(self, loader):
        """Reloading an unknown module should fail gracefully."""
        success = loader.reload_module("nonexistent_module")
        assert not success

    def test_reload_preserves_component_name(self, loader, plugin_dir):
        """After reload, the component name should still be correct."""
        loader.load_from_path(plugin_dir)
        loader.reload_module("my_validator")

        cls = loader.registry.get_validator("my_validator")
        assert cls is not None
        assert cls.name == "my_validator"

    def test_load_or_reload_file_new(self, loader, plugin_dir):
        """load_or_reload_file should load a new file."""
        new_file = plugin_dir / "new_plugin.py"
        new_file.write_text("""\
from selfheal.interfaces.store import StoreInterface

class NewStore(StoreInterface):
    name = "new_store"

    def save_events(self, events):
        pass

    def get_events(self, event_type=None, limit=100):
        return []

    def close(self):
        pass
""")

        success = loader.load_or_reload_file(new_file, plugin_dir)
        assert success
        assert loader.registry.get_store("new_store") is not None

    def test_load_or_reload_file_existing(self, loader, plugin_dir):
        """load_or_reload_file on existing module should reload it."""
        loader.load_from_path(plugin_dir)

        original = loader.registry.get_validator("my_validator")

        # Modify file
        validator_file = plugin_dir / "my_validator.py"
        validator_file.write_text(validator_file.read_text() + "\n# v3\n")

        success = loader.load_or_reload_file(validator_file, plugin_dir)
        assert success

        reloaded = loader.registry.get_validator("my_validator")
        assert reloaded is not original

    def test_load_or_reload_file_skips_non_py(self, loader, plugin_dir):
        """Non-.py files should be skipped."""
        file = plugin_dir / "readme.md"
        file.write_text("# Plugin docs")
        success = loader.load_or_reload_file(file, plugin_dir)
        assert not success

    def test_load_or_reload_file_skips_private(self, loader, plugin_dir):
        """Files starting with _ should be skipped."""
        file = plugin_dir / "_internal.py"
        file.write_text("# internal")
        success = loader.load_or_reload_file(file, plugin_dir)
        assert not success


class TestPluginLoaderInterfaceMap:
    """Tests for the interface map."""

    def test_interface_map_has_all_categories(self):
        imap = _get_interface_map()
        expected = {"stage", "watcher", "classifier", "patcher",
                     "validator", "reporter", "store"}
        assert set(imap.keys()) == expected

    @patch("selfheal.plugins.loader._INTERFACE_MAP", {})
    def test_interface_map_lazy_populated(self):
        """The map is populated lazily on first call."""
        from selfheal.plugins.loader import _INTERFACE_MAP
        assert _INTERFACE_MAP == {}
        imap = _get_interface_map()
        assert len(imap) == 7
        assert len(_INTERFACE_MAP) == 7


class TestPluginLoaderRegistration:
    """Tests for Registry integration."""

    def test_registry_names_after_load(self, loader, plugin_dir):
        """Registry.names() should include loaded plugins."""
        loader.load_from_path(plugin_dir)

        validator_names = loader.registry.names("validator")
        assert "my_validator" in validator_names

        reporter_names = loader.registry.names("reporter")
        assert "my_reporter" in reporter_names

    def test_duplicate_name_overwrites(self, loader, plugin_dir):
        """Loading a plugin with same name should overwrite."""
        loader.load_from_path(plugin_dir)

        first = loader.registry.get_validator("my_validator")

        # Create a new file with same component name
        (plugin_dir / "alt_validator.py").write_text("""\
from selfheal.interfaces.validator import ValidatorInterface
from selfheal.events import PatchEvent, ValidationEvent

class AltValidator(ValidatorInterface):
    name = "my_validator"

    def validate(self, patch: PatchEvent) -> ValidationEvent:
        return ValidationEvent(patch_event=patch, result="failed")
""")
        loader.load_or_reload_file(plugin_dir / "alt_validator.py", plugin_dir)

        second = loader.registry.get_validator("my_validator")
        assert second is not None
        assert second is not first  # overwritten

    def test_load_from_package_discovers_submodules(self, loader):
        """load_from_package should discover plugins in a package."""
        # We test with a known package that has submodules — use selfheal.core
        loader.load_from_package("selfheal.core.watchers")
        modules = loader.get_loaded_modules()
        # At least one watcher module should be tracked
        assert len(modules) > 0
