import itertools

import pytest

from metaskill.evaluate.models import (
    DEFAULT_WEIGHTS,
    Dimension,
    DimensionScore,
    DimensionStatus,
    EvaluationConfig,
    Finding,
    Scores,
    Severity,
    SkillResult,
    Status,
)
from metaskill.evaluate import rank as rank_module
from metaskill.evaluate.rank import table

ALL = {d: 0.5 for d in Dimension}
CONFIG = EvaluationConfig()


# The module's functions with the default weights, or the weights given.


def rank(results, weights=None):
    return rank_module.rank(results, EvaluationConfig(weights=weights) if weights else CONFIG)


def overall(found, weights=DEFAULT_WEIGHTS):
    return rank_module.overall(found, weights)


def weighted_mean(found, weights=DEFAULT_WEIGHTS):
    return rank_module.weighted_mean(found, weights)


def make_scores(dimension_scores):
    return rank_module.make_scores(dimension_scores, CONFIG)


def result(name, lift=0.0, dimensions=None, findings=(), **scores):
    """A SkillResult with every dimension at 0.5 unless given; None leaves one out."""
    found = {**{d.value: 0.5 for d in Dimension}, **scores}
    return SkillResult(
        name=name,
        scores=Scores(overall=None, **found),
        lift=lift,
        dimensions=dimensions or {},
        findings=list(findings),
    )


# --- overall --------------------------------------------------------------


def test_default_weights_follow_the_priority_order():
    w = DEFAULT_WEIGHTS
    assert w[Dimension.SAFETY] == 4
    assert w[Dimension.QUALITY] == w[Dimension.ROBUSTNESS] == 3
    assert w[Dimension.CLARITY] == w[Dimension.EFFICIENCY] == 2
    assert w[Dimension.CONFIG_CLEANLINESS] == 1
    assert EvaluationConfig().weights == DEFAULT_WEIGHTS


def test_overall_is_the_weighted_mean():
    found = {**ALL, Dimension.SAFETY: 1.0, Dimension.CONFIG_CLEANLINESS: 0.0}
    # (4*1 + 3*.5 + 3*.5 + 2*.5 + 2*.5 + 1*0) / 15
    assert overall(found) == pytest.approx(9 / 15)


def test_safety_moves_overall_more_than_config_cleanliness():
    low_safety = overall({**ALL, Dimension.SAFETY: 0.0})
    low_config = overall({**ALL, Dimension.CONFIG_CLEANLINESS: 0.0})
    assert low_safety < low_config


def test_overall_is_none_when_a_dimension_is_missing():
    assert overall({**ALL, Dimension.CLARITY: None}) is None


def test_a_missing_dimension_of_weight_zero_does_not_count():
    weights = {**DEFAULT_WEIGHTS, Dimension.CLARITY: 0}
    assert overall({**ALL, Dimension.CLARITY: None}, weights) == pytest.approx(0.5)


def test_weighted_mean_leaves_missing_dimensions_out():
    found = {**{d: None for d in Dimension}, Dimension.SAFETY: 1.0, Dimension.QUALITY: 0.0}
    assert weighted_mean(found) == pytest.approx(4 / 7)
    assert weighted_mean({d: None for d in Dimension}) is None


def test_make_scores_from_dimension_scores():
    scores = make_scores({d: DimensionScore(d, 0.5) for d in Dimension})
    assert scores.overall == pytest.approx(0.5)
    skipped = make_scores({
        **{d: DimensionScore(d, 0.5) for d in Dimension},
        Dimension.CLARITY: DimensionScore(Dimension.CLARITY, None, Status.SKIPPED),
    })
    assert skipped.clarity is None and skipped.overall is None
    assert make_scores({}).overall is None


@pytest.mark.parametrize("weights", [
    {Dimension.SAFETY: -1},
    {Dimension.SAFETY: float("inf")},
    {Dimension.SAFETY: float("nan")},
    {d: 0 for d in Dimension},
])
def test_invalid_weights_are_refused(weights):
    with pytest.raises(ValueError):
        EvaluationConfig(weights=weights)


def test_weights_above_one_are_accepted_and_round_trip():
    config = EvaluationConfig.from_dict(EvaluationConfig().to_dict())
    assert config.weights == DEFAULT_WEIGHTS


# --- ordering -------------------------------------------------------------


def test_ranked_by_overall():
    ranking = rank([result("low", quality=0.1), result("high", quality=0.9),
                    result("mid")])
    assert [e.result.name for e in ranking.ranked] == ["high", "mid", "low"]
    assert [e.rank for e in ranking.ranked] == [1, 2, 3]


def test_overall_is_recomputed_with_the_given_weights():
    a = result("a", safety=1.0, quality=0.0)
    b = result("b", safety=0.0, quality=1.0)
    assert rank([a, b]).ranked[0].result.name == "a"
    quality_first = {**DEFAULT_WEIGHTS, Dimension.QUALITY: 10}
    assert rank([a, b], quality_first).ranked[0].result.name == "b"


def test_ties_broken_by_lift_then_safety():
    a, b = result("a", lift=0.1), result("b", lift=0.3)
    assert [e.result.name for e in rank([a, b]).ranked] == ["b", "a"]
    # Same overall and lift: safety decides. Safety +0.3 and quality -0.4 keep the
    # weighted sum equal (4*.3 == 3*.4).
    c = result("c", safety=0.8, quality=0.1)
    d = result("d")
    assert rank([c, d]).ranked[0].overall == pytest.approx(rank([c, d]).ranked[1].overall)
    assert [e.result.name for e in rank([d, c]).ranked] == ["c", "d"]


def test_full_tie_falls_back_to_name():
    assert [e.result.name for e in rank([result("b"), result("a")]).ranked] == ["a", "b"]


def test_order_does_not_depend_on_input_order():
    skills = [result("a", quality=0.2), result("b", lift=0.4), result("c"),
              result("d", clarity=None), result("e", safety=0.9)]
    orders = {tuple(e.result.name for e in rank(list(p)).entries)
              for p in itertools.permutations(skills)}
    assert len(orders) == 1


def test_same_input_same_output():
    skills = [result("a", quality=0.2), result("b", clarity=None)]
    assert table(rank(skills)) == table(rank(skills))


# --- skills that cannot be ranked ----------------------------------------


def test_null_dimension_puts_the_skill_below_every_ranked_one():
    # "partial" scores 1.0 on everything it has, but is missing clarity.
    perfect_but_missing = result(
        "partial", clarity=None, **{d.value: 1.0 for d in Dimension if d is not Dimension.CLARITY})
    ranking = rank([perfect_but_missing, result("bad", quality=0.0, safety=0.0)])
    assert [e.result.name for e in ranking.ranked] == ["bad"]
    assert [e.result.name for e in ranking.unranked] == ["partial"]
    entry = ranking.unranked[0]
    assert entry.rank is None and entry.overall is None
    assert entry.partial_overall == pytest.approx(1.0)
    assert entry.missing == [Dimension.CLARITY]


def test_unranked_skills_get_a_note_on_how_they_did():
    skipped = DimensionStatus(Status.SKIPPED, detail="no API key")
    entry = rank([
        result("ranked", quality=0.4),
        result("partial", clarity=None, quality=0.9,
               dimensions={Dimension.CLARITY: skipped}),
    ]).unranked[0]
    note = entry.notes[0]
    assert note.startswith("Not ranked: no score for clarity (skipped: no API key).")
    assert "5 scored dimension(s)" in note
    assert "would place it 1st of 2" in note
    assert "best: quality (0.90)" in note


def test_unranked_skills_are_ordered_among_themselves():
    ranking = rank([result("x", clarity=None, quality=0.1),
                    result("y", clarity=None, quality=0.9),
                    result("z", **{d.value: None for d in Dimension})])
    assert [e.result.name for e in ranking.unranked] == ["y", "x", "z"]
    assert ranking.unranked[-1].partial_overall is None
    assert "No dimension was scored" in ranking.unranked[-1].notes[0]


# --- flags and recommendation --------------------------------------------


PARTIAL = DimensionStatus(Status.PARTIAL, detail="deep requested; static only")
OK = DimensionStatus(Status.OK)


def test_partial_safety_is_ranked_but_flagged_when_others_are_full():
    ranking = rank([result("a", quality=0.9, dimensions={Dimension.SAFETY: PARTIAL}),
                    result("b", dimensions={Dimension.SAFETY: OK})])
    entry = ranking.ranked[0]
    assert entry.rank == 1
    assert any("Safety is partial (deep requested; static only)" in n for n in entry.notes)
    assert not ranking.ranked[1].notes
    assert not ranking.notes
    assert "Check its safety notes" in ranking.recommendation


def test_partial_safety_for_every_skill_is_noted_once():
    ranking = rank([result("a", quality=0.9, dimensions={Dimension.SAFETY: PARTIAL}),
                    result("b", dimensions={Dimension.SAFETY: PARTIAL})])
    assert all(not e.notes for e in ranking.ranked)
    assert len(ranking.notes) == 1 and "partial for every skill" in ranking.notes[0]
    assert "Check its safety notes" not in ranking.recommendation
    assert "- Safety is partial for every skill" in table(ranking)


def test_critical_finding_keeps_the_skill_ranked():
    finding = Finding(Dimension.SAFETY, "destructive-unconfirmed", Severity.CRITICAL,
                      "own-checks", "recursive-delete", "rm -rf /", "SKILL.md:3")
    ranking = rank([result("a", safety=0.0, findings=[finding])])
    assert ranking.ranked[0].rank == 1
    assert "1 CRITICAL safety finding(s) at SKILL.md:3." in ranking.ranked[0].notes
    assert "Check its safety notes" in ranking.recommendation


def test_recommendation():
    assert rank([]).recommendation == "No skills to rank."
    assert rank([result("a"), result("b", quality=0.1)]).recommendation.startswith(
        "Use a: highest overall (0.50) of 2 ranked skill(s).")
    only_partial = rank([result("a", clarity=None)]).recommendation
    assert only_partial.startswith("No skill could be fully ranked")
    assert "a did best" in only_partial
    low = rank([result("a", safety=0.2)]).recommendation
    assert "safety score is low (0.20)" in low


def test_recommendation_warns_about_lift_of_zero_or_less():
    assert "Its lift is -0.60" in rank([result("a", lift=-0.6)]).recommendation
    assert "Its lift is +0.00" in rank([result("a", lift=0.0)]).recommendation
    assert "lift" not in rank([result("a", lift=0.2)]).recommendation
    # Without quality, lift was not measured, so it says nothing.
    no_quality = rank([result("a", lift=0.0, quality=None)]).recommendation
    assert "did best" in no_quality and "lift" not in no_quality
    best_unranked = rank([result("a", lift=-0.2, clarity=None)]).recommendation
    assert "Its lift is -0.20" in best_unranked


# --- untrusted text and invalid input ------------------------------------


def test_a_name_cannot_add_rows_or_escape_codes():
    forged = "evil\n| 1 | trusted | 0.99 | 1 | 1 | 1 | 1 | 1 | 1 |\x1b[2J\x07"
    text = table(rank([result(forged), result("trusted", quality=0.9)]))
    rows = [line for line in text.splitlines() if line.startswith("| ")]
    assert len(rows) == 3  # heading and two skills
    assert "\x1b" not in text and "\x07" not in text
    assert r"| 2 | evil \| 1 \| trusted" in text


def test_cleaned_notes():
    finding = Finding(Dimension.SAFETY, "c", Severity.CRITICAL, "own-checks", "r", "q",
                      "SKILL.md:3\n- fake: note\x1b[0m")
    skipped = DimensionStatus(Status.SKIPPED, detail="no key\r\nRecommendation: fake")
    text = table(rank([result("a", findings=[finding]),
                       result("b", clarity=None, dimensions={Dimension.CLARITY: skipped})]))
    assert "\x1b" not in text
    assert "\nRecommendation: fake" not in text and "\n- fake" not in text


def test_a_long_name_is_cut():
    entry = rank([result("x" * 500)]).ranked[0]
    assert len(entry.name) == rank_module.MAX_NAME_CHARS


def test_a_name_that_is_not_text_does_not_stop_the_ranking():
    ranking = rank([result(123), result("a")])
    assert [e.name for e in ranking.ranked] == ["123", "a"]
    assert "| 1 | 123 |" in table(ranking)


@pytest.mark.parametrize("lift", [float("nan"), 1.5, -1.01])
def test_lift_outside_its_range_is_refused(lift):
    with pytest.raises(ValueError):
        result("a", lift=lift)


def test_rank_checks_the_weights_it_is_given():
    config = EvaluationConfig()
    config.weights = {}  # changed after the config checked it
    with pytest.raises(ValueError):
        rank_module.rank([result("a")], config)
    with pytest.raises(ValueError):
        rank_module.make_scores({}, config)


def test_default_weights_are_read_only():
    with pytest.raises(TypeError):
        DEFAULT_WEIGHTS[Dimension.SAFETY] = 0  # type: ignore[index]


def test_weight_zero_dimensions_are_left_out_of_the_note():
    weights = {**{d: 1 for d in Dimension}, Dimension.CLARITY: 0}
    note = rank([result("z", efficiency=None, clarity=0.0)], weights).unranked[0].notes[0]
    assert "On the 4 scored dimension(s)" in note
    assert "clarity" not in note


def test_make_scores_and_rank_agree_on_overall():
    dimension_scores = {d: DimensionScore(d, v) for d, v in
                        zip(Dimension, (0.9, 0.2, 0.4, 0.7, 0.1, 0.6))}
    scores = make_scores(dimension_scores)
    entry = rank([SkillResult("a", scores, 0.0)]).ranked[0]
    assert entry.overall == pytest.approx(scores.overall)


def test_table():
    text = table(rank([result("good", quality=0.9), result("partial", clarity=None)]))
    lines = text.splitlines()
    assert lines[0] == ("| Rank | Skill | Overall | Quality | Robustness | Clarity "
                        "| Config Cleanliness | Efficiency | Safety |")
    assert lines[2].startswith("| 1 | good | 0.58 | 0.90 |")
    assert lines[3] == "| - | partial | (0.50) | 0.50 | 0.50 | - | 0.50 | 0.50 | 0.50 |"
    assert "Notes:" in lines
    assert lines[-1].startswith("Recommendation: Use good")
