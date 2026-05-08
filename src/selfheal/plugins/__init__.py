"""Plugin system for SelfHeal."""

from selfheal.plugins.loader import PluginLoader
from selfheal.plugins.sandbox import PluginSandbox

__all__ = ["PluginLoader", "PluginSandbox"]