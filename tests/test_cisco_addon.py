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

import pytest

from metaskill.evaluate.safety import cisco, missing_dependencies

REPO = Path(__file__).resolve().parent.parent
ADDON = REPO / "plugins" / "metaskill-cisco"


def _dependencies(manifest: Path) -> list[str]:
    text = manifest.read_text(encoding="utf-8")
    block = re.search(r"^python_dependencies:\s*\n((?:\s+-\s*.+\n?)+)", text, re.M)
    return re.findall(r'-\s*"([^"]+)"', block.group(1)) if block else []


def declares_python_dependencies(text: str) -> bool:
    """Whether a manifest declares Python packages in any way Hermes reads
    (hermes_cli/plugins_loader._declared_requirements): either key, block or flow style, quoted
    or not. Deliberately blunt: any such key counts unless its value is an empty list, so no
    spelling of a list can slip past it, and no YAML parser (CI has only pytest) is needed."""
    for match in re.finditer(r"^[ \t]*(?:python|pip)_dependencies[ \t]*:(.*)$", text, re.M):
        if match.group(1).split("#")[0].strip() != "[]":
            return True
    return False


def test_metaskill_itself_declares_no_python_dependencies():
    assert not declares_python_dependencies((REPO / "plugin.yaml").read_text(encoding="utf-8"))
    # An authored pyproject.toml takes precedence over the manifest in Hermes.
    assert not (REPO / "pyproject.toml").exists()


@pytest.mark.parametrize("text", [
    'python_dependencies: ["cisco-ai-skill-scanner==2.2.1"]\n',
    "python_dependencies:\n  - cisco-ai-skill-scanner==2.2.1\n",
    "python_dependencies:\n  - \"cisco-ai-skill-scanner==2.2.1\"\n",
    "pip_dependencies:\n  - cisco-ai-skill-scanner==2.2.1\n",
    "python_dependencies: [cisco-ai-skill-scanner==2.2.1]  # the scanner\n",
    "name: x\n  python_dependencies: [a]\n",
], ids=["flow", "block unquoted", "block quoted", "pip_dependencies", "flow with comment", "indented"])
def test_the_guard_sees_every_way_hermes_reads_a_dependency(text):
    # The guard above is only as good as this: a reader that missed one of these would let the
    # original bug (metaskill declaring the scanner) come back unnoticed.
    assert declares_python_dependencies(text)


@pytest.mark.parametrize("text", [
    "name: metaskill\nversion: 0.2.0\n",
    "python_dependencies: []\n",
    "pip_dependencies: []  # none\n",
    "# python_dependencies: [cisco]\n",
], ids=["no key", "empty list", "empty list with comment", "commented out"])
def test_the_guard_accepts_a_manifest_that_declares_nothing(text):
    assert not declares_python_dependencies(text)


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
