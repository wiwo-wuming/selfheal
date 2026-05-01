"""Tests for the generic and convenience Registry APIs."""

import pytest

from selfheal.registry import Registry, get_registry


class DummyComponent:
    """Fake component class for testing registration."""
    pass


class TestRegistryGenericAPI:
    """Test Registry.register(), .get(), .names() generic methods."""

    def test_register_valid_category(self):
        reg = Registry()
        reg.register("watcher", "dummy", DummyComponent)
        assert reg.get("watcher", "dummy") is DummyComponent

    def test_register_invalid_category_raises(self):
        reg = Registry()
        with pytest.raises(ValueError, match="Unknown component category"):
            reg.register("not_a_real_category", "x", DummyComponent)

    def test_get_unknown_category_returns_none(self):
        reg = Registry()
        assert reg.get("nonexistent", "x") is None

    def test_get_unknown_name_returns_none(self):
        reg = Registry()
        assert reg.get("watcher", "no_such_name") is None

    def test_names_populated(self):
        reg = Registry()
        reg.register("watcher", "alpha", DummyComponent)
        reg.register("watcher", "beta", DummyComponent)
        reg.register("classifier", "gamma", DummyComponent)

        names_w = reg.names("watcher")
        assert sorted(names_w) == ["alpha", "beta"]

        names_c = reg.names("classifier")
        assert names_c == ["gamma"]

    def test_names_empty_category(self):
        reg = Registry()
        assert reg.names("store") == []

    def test_names_unknown_category(self):
        reg = Registry()
        assert reg.names("bogus") == []

    def test_register_overwrites_existing(self):
        reg = Registry()
        class Old(DummyComponent): pass
        class New(DummyComponent): pass
        reg.register("patcher", "dup", Old)
        reg.register("patcher", "dup", New)
        assert reg.get("patcher", "dup") is New

    def test_all_categories_initialised_empty(self):
        reg = Registry()
        for cat in Registry._CATEGORIES:
            assert reg.names(cat) == []


class TestRegistryConvenienceWrappers:
    """Test the backward-compatible typed methods."""

    def test_register_watcher(self):
        reg = Registry()
        reg.register_watcher("pytest", DummyComponent)
        assert reg.get_watcher("pytest") is DummyComponent
        assert reg.get("watcher", "pytest") is DummyComponent

    def test_register_classifier(self):
        reg = Registry()
        reg.register_classifier("rule", DummyComponent)
        assert reg.get_classifier("rule") is DummyComponent

    def test_register_patcher(self):
        reg = Registry()
        reg.register_patcher("template", DummyComponent)
        assert reg.get_patcher("template") is DummyComponent

    def test_register_validator(self):
        reg = Registry()
        reg.register_validator("local", DummyComponent)
        assert reg.get_validator("local") is DummyComponent

    def test_register_reporter(self):
        reg = Registry()
        reg.register_reporter("terminal", DummyComponent)
        assert reg.get_reporter("terminal") is DummyComponent

    def test_register_store(self):
        reg = Registry()
        reg.register_store("sqlite", DummyComponent)
        assert reg.get_store("sqlite") is DummyComponent

    def test_register_stage(self):
        reg = Registry()
        reg.register_stage("classify", DummyComponent)
        assert reg.get_stage("classify") is DummyComponent

    def test_convenience_names_properties(self):
        reg = Registry()
        reg.register("watcher", "alice", DummyComponent)
        reg.register("watcher", "bob", DummyComponent)
        reg.register("classifier", "charlie", DummyComponent)

        assert sorted(reg.watcher_names) == ["alice", "bob"]
        assert reg.classifier_names == ["charlie"]
        assert reg.patcher_names == []

    def test_all_names_properties_exist(self):
        reg = Registry()
        for cat in Registry._CATEGORIES:
            prop_name = f"{cat}_names"
            assert hasattr(Registry, prop_name), f"Missing property: {prop_name}"
            value = getattr(reg, prop_name)
            assert isinstance(value, list)


class TestGlobalRegistry:
    """Test the global singleton registry."""

    def test_get_registry_returns_same_instance(self):
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2

    def test_global_registry_is_a_registry(self):
        r = get_registry()
        assert isinstance(r, Registry)
