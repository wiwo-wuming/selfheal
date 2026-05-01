"""Plugin loader for SelfHeal — with hot-reloading support."""

import importlib
import logging
import pkgutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from selfheal.registry import get_registry

logger = logging.getLogger(__name__)

# Extracted as class-level so it's reusable across the loader and hot-reloader.
_INTERFACE_MAP: dict[str, type] = {}  # populated lazily on first use


def _get_interface_map() -> dict[str, type]:
    """Lazily build the category→interface mapping."""
    if _INTERFACE_MAP:
        return _INTERFACE_MAP

    from selfheal.interfaces import (
        WatcherInterface,
        ClassifierInterface,
        PatcherInterface,
        ValidatorInterface,
        ReporterInterface,
        StoreInterface,
        PipelineStage,
    )

    _INTERFACE_MAP.update({
        "stage":      PipelineStage,
        "watcher":    WatcherInterface,
        "classifier": ClassifierInterface,
        "patcher":    PatcherInterface,
        "validator":  ValidatorInterface,
        "reporter":   ReporterInterface,
        "store":      StoreInterface,
    })
    return _INTERFACE_MAP


class PluginLoader:
    """Loads and registers SelfHeal plugins with hot-reloading support.

    Tracks loaded modules so they can be reloaded at runtime via
    :meth:`reload_module` without restarting the process.
    """

    def __init__(self):
        self.registry = get_registry()
        self._loaded_plugins: list[type] = []
        # Track module import paths → module objects for hot-reloading
        self._loaded_modules: dict[str, ModuleType] = {}
        # Track base directories for modules loaded from path
        # (module_name → resolved Path of the containing directory)
        self._loaded_module_dirs: dict[str, Path] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_from_package(self, package_name: str) -> None:
        """Load all plugins from a Python package."""
        try:
            package = importlib.import_module(package_name)
            self._discover_plugins(package)
            logger.info(f"Loaded plugins from {package_name}")
        except ImportError as e:
            logger.error(f"Failed to import plugin package {package_name}: {e}")

    def load_from_path(self, path: Path) -> None:
        """Load plugins from a directory path.

        Each ``.py`` file in *path* is imported as a top-level module and
        registered.  The directory is temporarily added to ``sys.path`` if
        it is not already there.
        """
        if not path.exists() or not path.is_dir():
            logger.warning(f"Plugin path does not exist: {path}")
            return

        resolved_path = path.resolve()
        path_str = str(resolved_path)
        path_in_syspath = path_str in sys.path
        if not path_in_syspath:
            sys.path.insert(0, path_str)

        try:
            for finder, name, ispkg in pkgutil.iter_modules([path_str]):
                # Skip private modules (e.g. _internal.py)
                if name.startswith("_"):
                    continue
                try:
                    module = importlib.import_module(name)
                    self._register_plugin_module(module, module_path=name)
                    # Track the base directory for reliable hot-reloading
                    self._loaded_module_dirs[name] = resolved_path
                    logger.info(f"Loaded plugin: {name}")
                except Exception as e:
                    logger.error(f"Failed to load plugin {name}: {e}")
        finally:
            if not path_in_syspath:
                sys.path.remove(path_str)

    def reload_module(self, module_name: str) -> bool:
        """Hot-reload a previously loaded module and re-register its components.

        Uses the tracked base directory (set during :meth:`load_from_path`)
        to reliably locate the module file.  Falls back to standard
        ``importlib.reload`` for modules loaded from packages.

        Returns True if the module was successfully reloaded, False otherwise.
        """
        module = self._loaded_modules.get(module_name)
        if module is None:
            logger.warning(f"Cannot reload unknown module: {module_name}")
            return False

        try:
            logger.info(f"Hot-reloading module: {module_name}")

            import importlib.util

            # Use the tracked base directory whenever available — this is
            # the most reliable way to locate the file, especially when
            # modules were loaded from temporary directories on Windows.
            base_dir = self._loaded_module_dirs.get(module_name)
            if base_dir is not None:
                module_file = str(base_dir / f"{module_name}.py")
                spec = importlib.util.spec_from_file_location(
                    module_name, module_file
                )
                if spec is None:
                    raise ImportError(
                        f"Cannot create spec for module: {module_name}"
                    )

                # Ensure the parent dir is on sys.path for sub-imports
                base_str = str(base_dir)
                path_added = False
                if base_str not in sys.path:
                    sys.path.insert(0, base_str)
                    path_added = True

                try:
                    reloaded = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = reloaded
                    spec.loader.exec_module(reloaded)

                    self._register_plugin_module(
                        reloaded, module_path=module_name
                    )
                    self._loaded_modules[module_name] = reloaded
                    return True
                finally:
                    if path_added:
                        sys.path.remove(base_str)
            else:
                # Fallback for modules loaded from packages:
                # ensure the module dir is on sys.path and use standard reload
                module_dir = self._get_module_dir(module)
                path_added = False
                if module_dir and module_dir not in sys.path:
                    sys.path.insert(0, module_dir)
                    path_added = True

                try:
                    reloaded = importlib.reload(module)
                    self._register_plugin_module(
                        reloaded, module_path=module_name
                    )
                    self._loaded_modules[module_name] = reloaded
                    return True
                finally:
                    if path_added and module_dir:
                        sys.path.remove(module_dir)
        except Exception as e:
            logger.error(f"Failed to reload module {module_name}: {e}")
            return False

    @staticmethod
    def _get_module_dir(module: ModuleType) -> str:
        """Return the directory containing *module*'s source file."""
        module_file = getattr(module, "__file__", None)
        if module_file:
            return str(Path(module_file).resolve().parent)
        return ""

    def load_or_reload_file(self, file_path: Path, base_dir: Path) -> bool:
        """Load or reload a single ``.py`` file as a plugin.

        Derives the module name from *file_path* relative to *base_dir*.
        If the module was previously loaded it is reloaded; otherwise
        it is imported for the first time.
        """
        if not file_path.suffix == ".py" or file_path.name.startswith("_"):
            return False

        try:
            # Derive module name: path relative to base_dir, strip .py suffix
            rel = file_path.relative_to(base_dir)
            module_name = str(rel.with_suffix("")).replace("/", ".").replace("\\", ".")

            base_str = str(base_dir.resolve())
            path_in_syspath = base_str in sys.path
            if not path_in_syspath:
                sys.path.insert(0, base_str)

            try:
                if module_name in self._loaded_modules:
                    return self.reload_module(module_name)
                else:
                    module = importlib.import_module(module_name)
                    self._register_plugin_module(module, module_path=module_name)
                    # Track base dir for future hot-reloading
                    self._loaded_module_dirs[module_name] = base_dir.resolve()
                    logger.info(f"Loaded new plugin: {module_name}")
                    return True
            finally:
                if not path_in_syspath:
                    sys.path.remove(base_str)
        except Exception as e:
            logger.error(f"Failed to load/reload plugin {file_path}: {e}")
            return False

    def get_loaded_plugins(self) -> list[type]:
        """Get list of loaded plugin classes."""
        return self._loaded_plugins

    def get_loaded_modules(self) -> dict[str, ModuleType]:
        """Get dict of module_name → module for all loaded plugin modules."""
        return dict(self._loaded_modules)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _discover_plugins(self, package: Any) -> None:
        """Discover plugins in a Python package."""
        if not hasattr(package, "__path__"):
            return

        for finder, name, ispkg in pkgutil.iter_modules(package.__path__):
            try:
                full_name = f"{package.__name__}.{name}"
                module = importlib.import_module(full_name)
                self._register_plugin_module(module, module_path=full_name)
            except Exception as e:
                logger.error(f"Failed to discover plugin {name}: {e}")

    def _register_plugin_module(self, module: Any, module_path: str = "") -> None:
        """Register component classes from a plugin module.

        Every public class in *module* that has a ``name`` class attribute
        and is a subclass of a known interface is registered in the global
        :class:`Registry`.
        """
        interface_map = _get_interface_map()

        for attr_name in dir(module):
            attr = getattr(module, attr_name)

            # Skip private/abstract classes
            if attr_name.startswith("_"):
                continue
            if not isinstance(attr, type):
                continue

            # Get the component name from class attribute
            component_name = getattr(attr, "name", None)
            if not isinstance(component_name, str):
                continue

            # Try each known category
            for category, interface in interface_map.items():
                if issubclass(attr, interface) and attr is not interface:
                    self.registry.register(category, component_name, attr)
                    self._loaded_plugins.append(attr)
                    break  # a class belongs to exactly one category

        # Track the module for future hot-reloading
        if module_path and isinstance(module, ModuleType):
            self._loaded_modules[module_path] = module
