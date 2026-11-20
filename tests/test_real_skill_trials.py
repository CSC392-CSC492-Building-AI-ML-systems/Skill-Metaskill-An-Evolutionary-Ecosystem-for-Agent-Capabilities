"""MET-17: generalize then personalize on four real Hermes skills (tests/real_skill_trials.py).

These pin the results recorded in docs/real-skill-trials.md. A change that alters a false
replacement, a miss or the round trip fails here, so the record is updated with it.
"""

import pytest

from real_skill_trials import run_all

# Known limitation (docs/real-skill-trials.md): placeholder addresses on company.com are
# replaced, because the detector treats company.com as a real domain on purpose.
KNOWN_FALSE_REPLACEMENTS = {
    "email/himalaya": ["references/configuration.md:172 (email)"],
    "productivity/google-workspace": ["references/gmail-search-syntax.md:53 (email)"],
    "productivity/maps": [],
    "social-media/xurl": [],
}


@pytest.fixture(scope="module")
def trials():
    return {t.skill: t for t in run_all()}


def test_every_annotated_skill_is_tried(trials):
    assert sorted(trials) == sorted(KNOWN_FALSE_REPLACEMENTS)


@pytest.mark.parametrize("skill", sorted(KNOWN_FALSE_REPLACEMENTS))
def test_round_trip_restores_the_skill_exactly(trials, skill):
    trial = trials[skill]
    assert trial.generalize_status in ("ok", "partial"), trial.warnings
    assert trial.personalize_status == "ok", trial.warnings
    assert trial.round_trip_identical, trial.changed_files
    assert trial.frontmatter_intact
    assert trial.hermes_loads in (None, True)  # None when Hermes is not installed


@pytest.mark.parametrize("skill", sorted(KNOWN_FALSE_REPLACEMENTS))
def test_detection_matches_the_recorded_result(trials, skill):
    trial = trials[skill]
    assert trial.missed == []
    assert trial.false_replacements == KNOWN_FALSE_REPLACEMENTS[skill]


@pytest.mark.parametrize("skill", sorted(KNOWN_FALSE_REPLACEMENTS))
def test_values_left_for_review_are_not_reported_as_residuals(trials, skill):
    assert not any("residual_candidates" in w for w in trials[skill].warnings)


def test_tool_results_stay_small(trials):
    for trial in trials.values():
        assert trial.tokens_generalize_result < 1000
        assert trial.tokens_personalize_result < 500
