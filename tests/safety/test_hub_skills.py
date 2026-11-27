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
    # openai keeps a licence in each skill; the others also have one for the source.
    for path in hub_skills.skill_dirs():
        source = hub_skills.ROOT / hub_skills.skill_id(path).split("/")[0]
        assert any(path.glob("LICENSE*")) or (source / "LICENSE").is_file(), path


@pytest.mark.parametrize("path", hub_skills.skill_dirs(), ids=hub_skills.skill_id)
def test_each_skill_loads_with_all_its_files(path):
    skill = load(path)
    assert skill.body.startswith("---")
    assert all((path / f).is_file() for f in skill.files)


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
