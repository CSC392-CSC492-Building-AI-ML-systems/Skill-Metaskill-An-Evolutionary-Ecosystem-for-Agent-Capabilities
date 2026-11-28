"""The own checks on the skills from the Hermes hub's default sources, compared with a
saved snapshot, as test_hermes_skills.py does for the bundled ones.

The snapshot records what the checks find today, false positives included. If a change is
intended, refresh it with tests/safety/update_hermes_snapshot.py and review the diff of
hub_expected.json.
"""

import pytest

import hub_skills
from hermes_skills import load
from hermes_snapshot import HUB_SNAPSHOT, expected, results


def test_every_source_and_its_licence_is_present():
    ids = [hub_skills.skill_id(p) for p in hub_skills.skill_dirs()]
    sources = [i.split("/")[0] for i in ids]
    assert {s: sources.count(s) for s in sources} == {
        "anthropics": 6, "huggingface": 4, "openai": 7}
    # anthropics and openai give each skill its licence; huggingface has one for all.
    for path in hub_skills.skill_dirs():
        source = hub_skills.skill_id(path).split("/")[0]
        licence = (hub_skills.ROOT / source / "LICENSE" if source == "huggingface"
                   else path / "LICENSE.txt")
        assert licence.is_file(), path


def test_source_md_names_every_skill():
    text = (hub_skills.ROOT / "SOURCE.md").read_text(encoding="utf-8")
    for path in hub_skills.skill_dirs():
        assert f"| `{hub_skills.skill_id(path)}` |" in text, path


@pytest.mark.parametrize("path", hub_skills.skill_dirs(), ids=hub_skills.skill_id)
def test_each_skill_loads_with_all_its_files(path):
    skill = load(path)
    assert skill.body.startswith("---")
    # Nothing is hidden from the tests by the loader: it lists every file but SKILL.md.
    assert len(skill.files) == sum(f.is_file() and "__pycache__" not in f.parts
                                   for f in path.rglob("*")) - 1


@pytest.fixture(scope="module")
def actual():
    return results(hub_skills)


@pytest.mark.parametrize("skill", sorted(expected(HUB_SNAPSHOT)))
def test_findings_match_the_snapshot(actual, skill):
    assert actual[skill] == expected(HUB_SNAPSHOT)[skill], (
        f"{skill}: findings changed. If intended, run tests/safety/update_hermes_snapshot.py "
        "and review the diff of hub_expected.json.")


def test_snapshot_covers_every_skill(actual):
    assert set(actual) == set(expected(HUB_SNAPSHOT))
