"""The own checks on the 58 skills bundled with Hermes, compared with a saved snapshot.

A rule change that adds or removes a finding on a real skill makes this fail. If the
change is intended, refresh the snapshot with tests/safety/update_hermes_snapshot.py
and review the diff of hermes_expected.json.
"""

import pytest

from hermes_skills import skill_dirs
from hermes_snapshot import expected, results


def test_all_58_skills_are_present():
    assert len(skill_dirs()) == 58


@pytest.fixture(scope="module")
def actual():
    return results()


@pytest.mark.parametrize("skill", sorted(expected()))
def test_findings_match_the_snapshot(actual, skill):
    assert actual[skill] == expected()[skill], (
        f"{skill}: findings changed. If intended, run tests/safety/update_hermes_snapshot.py "
        "and review the diff of hermes_expected.json.")


def test_snapshot_covers_every_skill(actual):
    assert set(actual) == set(expected())
