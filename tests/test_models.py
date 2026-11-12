"""Skill.load's frontmatter, read through Hermes' YAML loader."""

import sys
import types

import pytest

from metaskill.evaluate.models import Skill, load_yaml

BODY = "---\nname: csv-summary\ndescription: Summarize a CSV file.\n---\n\n# Skill\n"


def write(folder, body=BODY):
    (folder / "SKILL.md").write_text(body, encoding="utf-8")
    return folder


@pytest.fixture
def loader(monkeypatch):
    """A stand-in for hermes_yaml, which only exists inside Hermes. Records what it read."""
    read = []

    def safe_load(text):
        read.append(text)
        return {"name": "csv-summary"}

    monkeypatch.setitem(sys.modules, "hermes_yaml", types.SimpleNamespace(safe_load=safe_load))
    return read


def test_frontmatter_goes_through_hermes_loader(tmp_path, loader):
    skill = Skill.load(write(tmp_path))
    assert loader == ["name: csv-summary\ndescription: Summarize a CSV file."]
    assert skill.name == "csv-summary" and skill.frontmatter == {"name": "csv-summary"}
    assert skill.body == BODY  # the whole file, so line numbers match SKILL.md


def test_a_skill_without_frontmatter_needs_no_loader(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_yaml", None)
    assert Skill.load(write(tmp_path, "# Skill\n")).name == tmp_path.name


def test_frontmatter_outside_hermes_says_what_is_missing(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_yaml", None)
    with pytest.raises(ImportError, match="hermes_yaml"):
        Skill.load(write(tmp_path))


def test_frontmatter_that_is_not_a_mapping_is_an_error(tmp_path, monkeypatch):
    fake = types.SimpleNamespace(safe_load=lambda text: ["a", "list"])
    monkeypatch.setitem(sys.modules, "hermes_yaml", fake)
    with pytest.raises(ValueError, match="not a mapping"):
        Skill.load(write(tmp_path))


class CannotPrint:
    """A frontmatter value that must never be turned into text."""

    def __str__(self):
        raise AssertionError("str() was called on a frontmatter value")

    __repr__ = __str__


@pytest.mark.parametrize("value, expected", [
    (CannotPrint(), None), (["a", ["b"]], None), ({"x": 1}, None), (True, None),
    ("", None), ("  ", None), (2024, "2024"), (1.5, "1.5"),
])
def test_only_a_text_or_number_name_is_used(tmp_path, monkeypatch, value, expected):
    fake = types.SimpleNamespace(safe_load=lambda text: {"name": value})
    monkeypatch.setitem(sys.modules, "hermes_yaml", fake)
    assert Skill.load(write(tmp_path)).name == (expected or tmp_path.name)


def test_real_hermes_loader_reads_frontmatter(tmp_path):
    pytest.importorskip("hermes_yaml")  # only inside Hermes
    assert load_yaml("name: a\nenabled: yes\n") == {"name": "a", "enabled": True}
    assert Skill.load(write(tmp_path)).name == "csv-summary"
