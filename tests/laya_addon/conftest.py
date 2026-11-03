"""Setup for the metaskill-laya add-on tests: load the add-on like Hermes does.

Hermes imports a plugin folder as ``hermes_plugins.<name>``, so the add-on's relative
imports work and it never shares a module name with metaskill. Tests marked ``slow``
run the real model (about 840 MB, downloaded once) and are skipped unless
METASKILL_RUN_SLOW=1 is set.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ADDON_DIR = HERE.parents[1] / "plugins" / "metaskill-laya"
SLOW_ENV = "METASKILL_RUN_SLOW"


def load_addon():
    if "hermes_plugins" not in sys.modules:
        namespace = types.ModuleType("hermes_plugins")
        namespace.__path__ = []
        sys.modules["hermes_plugins"] = namespace
    name = "hermes_plugins.metaskill_laya"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, ADDON_DIR / "__init__.py", submodule_search_locations=[str(ADDON_DIR)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def pytest_configure(config):
    config.addinivalue_line(
        "markers", f"slow: runs the real Laya model (~840 MB download); set {SLOW_ENV}=1 to include")


def pytest_collection_modifyitems(config, items):
    if os.environ.get(SLOW_ENV):
        return
    skip_slow = pytest.mark.skip(reason=f"needs {SLOW_ENV}=1 (runs the Laya model)")
    for item in items:
        if "slow" in item.keywords and Path(str(item.path)).is_relative_to(HERE):
            item.add_marker(skip_slow)


@pytest.fixture
def addon():
    return load_addon()


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """No test leaks a registered detector or a loaded model into the next."""
    plugin = load_addon()
    registry = plugin._registry()
    saved = dict(registry)
    registry.clear()
    monkeypatch.setattr(plugin.detector, "_MODELS", {})
    for env in (plugin.detector.DEVICE_ENV, plugin.detector.MODEL_ENV):
        monkeypatch.delenv(env, raising=False)
    yield
    registry.clear()
    registry.update(saved)


class FakeCtx:
    """The parts of Hermes's PluginContext the add-on uses."""

    def __init__(self):
        self.unload = []
        self.cli = []
        self.skills = []

    def on_unload(self, callback):
        self.unload.append(callback)

    def register_skill(self, name, path, description="", frontmatter=None):
        self.skills.append((name, path, description, frontmatter))

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
        self.cli.append((name, setup_fn, handler_fn))


@pytest.fixture
def ctx():
    return FakeCtx()
