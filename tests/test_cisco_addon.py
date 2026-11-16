"""The metaskill-cisco add-on (plugins/metaskill-cisco): Cisco's scanner is optional.

metaskill itself declares no Python dependencies, so enabling it installs nothing and
declining nothing can leave it disabled. The add-on declares the scanner, pinned to the
version metaskill's adapter is tested with.
"""

import importlib.util
import re
import sys
import types
from pathlib import Path

from metaskill.evaluate.safety import cisco, missing_dependencies

REPO = Path(__file__).resolve().parent.parent
ADDON = REPO / "plugins" / "metaskill-cisco"


def _dependencies(manifest: Path) -> list[str]:
    text = manifest.read_text(encoding="utf-8")
    block = re.search(r"^python_dependencies:\s*\n((?:\s+-\s*.+\n?)+)", text, re.M)
    return re.findall(r'-\s*"([^"]+)"', block.group(1)) if block else []


def test_metaskill_itself_declares_no_python_dependencies():
    assert _dependencies(REPO / "plugin.yaml") == []


def test_the_addon_pins_the_version_the_adapter_is_tested_with():
    assert _dependencies(ADDON / "plugin.yaml") == [f"{cisco.PACKAGE}=={cisco.TESTED_VERSION}"]


def test_dev_requirements_match_the_addon_manifest():
    lines = (ADDON / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert [line for line in lines if line and not line.startswith("#")] == _dependencies(ADDON / "plugin.yaml")


def test_the_addon_requires_metaskill_and_is_named_as_the_install_command_says():
    manifest = (ADDON / "plugin.yaml").read_text(encoding="utf-8")
    assert re.search(r"^name: metaskill-cisco$", manifest, re.M)
    assert re.search(r"requires_plugins:\s*\n\s*-\s*id: metaskill\b", manifest)
    assert cisco.ADDON == "metaskill-cisco"
    assert cisco.INSTALL_COMMAND.startswith("hermes plugins install ")
    assert cisco.INSTALL_COMMAND.endswith("#plugins/metaskill-cisco")


def test_the_addon_loads_as_a_package_under_its_own_name(monkeypatch):
    # As Hermes loads it: not on sys.path, under hermes_plugins.<name>, and it must not
    # import metaskill (a separate plugin it cannot reach).
    monkeypatch.setitem(sys.modules, "hermes_plugins", types.ModuleType("hermes_plugins"))
    spec = importlib.util.spec_from_file_location(
        "hermes_plugins.metaskill_cisco", ADDON / "__init__.py", submodule_search_locations=[str(ADDON)])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    class Ctx:
        def __getattr__(self, name):
            return lambda *args, **kwargs: calls.append(name)

    assert module.register(Ctx()) is None and calls == []
    assert "metaskill" not in (ADDON / "__init__.py").read_text(encoding="utf-8").split('"""')[2]


def test_without_the_scanner_the_dependency_names_the_addon(monkeypatch):
    monkeypatch.setattr(cisco.CiscoBackend, "available", lambda self: False)
    [dep] = missing_dependencies()
    assert dep.package == cisco.PACKAGE and not dep.required and dep.install_command == ""
    assert "metaskill-cisco" in dep.reason and cisco.INSTALL_COMMAND in dep.reason
