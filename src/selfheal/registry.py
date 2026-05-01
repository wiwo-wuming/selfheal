"""Component registry for SelfHeal.

The registry stores component classes by category (watcher, classifier,
patcher, validator, reporter, store, stage) so that the engine and plugin
loader can look them up at runtime.

The public API is the set of typed convenience methods
(``register_watcher``, ``get_classifier``, …).  Internally the unified
``register`` / ``get`` / ``names`` generic methods keep the implementation
DRY and make it trivial to add new component categories.
"""

from typing import Optional, Type


class Registry:
    """Component registry with both generic and typed convenience APIs."""

    _CATEGORIES = (
        "watcher",
        "classifier",
        "patcher",
        "validator",
        "reporter",
        "store",
        "stage",
    )

    def __init__(self):
        self._components: dict[str, dict[str, Type]] = {
            c: {} for c in self._CATEGORIES
        }

    # ------------------------------------------------------------------
    # Generic (unified) API — used internally by PluginLoader & friends
    # ------------------------------------------------------------------

    def register(self, category: str, name: str, cls: Type) -> None:
        """Register a component class under *category*.

        Raises:
            ValueError: if *category* is not a known component category.
        """
        if category not in self._components:
            raise ValueError(
                f"Unknown component category: {category!r}. "
                f"Valid categories: {', '.join(self._CATEGORIES)}"
            )
        self._components[category][name] = cls

    def get(self, category: str, name: str) -> Optional[Type]:
        """Look up a registered component class.

        Returns ``None`` when the category or name is unknown.
        """
        return self._components.get(category, {}).get(name)

    def names(self, category: str) -> list[str]:
        """Return the names of all components registered under *category*."""
        return list(self._components.get(category, {}).keys())

    # ------------------------------------------------------------------
    # Typed convenience API — backward-compatible wrappers
    # ------------------------------------------------------------------

    def register_watcher(self, name: str, cls: Type) -> None:
        self.register("watcher", name, cls)

    def register_classifier(self, name: str, cls: Type) -> None:
        self.register("classifier", name, cls)

    def register_patcher(self, name: str, cls: Type) -> None:
        self.register("patcher", name, cls)

    def register_validator(self, name: str, cls: Type) -> None:
        self.register("validator", name, cls)

    def register_reporter(self, name: str, cls: Type) -> None:
        self.register("reporter", name, cls)

    def register_store(self, name: str, cls: Type) -> None:
        self.register("store", name, cls)

    def register_stage(self, name: str, cls: Type) -> None:
        self.register("stage", name, cls)

    def get_watcher(self, name: str) -> Optional[Type]:
        return self.get("watcher", name)

    def get_classifier(self, name: str) -> Optional[Type]:
        return self.get("classifier", name)

    def get_patcher(self, name: str) -> Optional[Type]:
        return self.get("patcher", name)

    def get_validator(self, name: str) -> Optional[Type]:
        return self.get("validator", name)

    def get_reporter(self, name: str) -> Optional[Type]:
        return self.get("reporter", name)

    def get_store(self, name: str) -> Optional[Type]:
        return self.get("store", name)

    def get_stage(self, name: str) -> Optional[Type]:
        return self.get("stage", name)

    @property
    def watcher_names(self) -> list[str]:
        return self.names("watcher")

    @property
    def classifier_names(self) -> list[str]:
        return self.names("classifier")

    @property
    def patcher_names(self) -> list[str]:
        return self.names("patcher")

    @property
    def validator_names(self) -> list[str]:
        return self.names("validator")

    @property
    def reporter_names(self) -> list[str]:
        return self.names("reporter")

    @property
    def store_names(self) -> list[str]:
        return self.names("store")

    @property
    def stage_names(self) -> list[str]:
        return self.names("stage")


# Global registry instance
_global_registry = Registry()


def get_registry() -> Registry:
    return _global_registry
