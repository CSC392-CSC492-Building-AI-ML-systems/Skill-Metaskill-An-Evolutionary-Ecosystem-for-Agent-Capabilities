import itertools
import re

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


def test_a_missing_dimension_of_weight_zero_still_means_no_overall():
    weights = {**DEFAULT_WEIGHTS, Dimension.CLARITY: 0}
    assert overall({**ALL, Dimension.CLARITY: None}, weights) is None


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
    {Dimension.SAFETY: "4"},
    {Dimension.SAFETY: True},
    {Dimension.SAFETY: None},
    {"bogus": 1},  # sums above 0, yet weighs none of the six: overall would divide by 0
    {"safety": 1},  # a Dimension's value, but to_dict needs the Dimension
    {d: 1e308 for d in Dimension},  # each finite, the sum not: overall would be NaN
])
def test_invalid_weights_are_refused(weights):
    with pytest.raises(ValueError):
        EvaluationConfig(weights=weights)


@pytest.mark.parametrize("call", [
    lambda config: rank_module.rank([result("a")], config),
    lambda config: rank_module.make_scores({}, config),
])
def test_unknown_weights_set_after_the_config_was_made_are_refused(call):
    config = EvaluationConfig()
    config.weights = {"bogus": 1}
    with pytest.raises(ValueError, match="unknown dimension"):
        call(config)


def test_text_weight_from_a_config_file_is_a_clear_error():
    with pytest.raises(ValueError, match="must be a number, got '4'"):
        EvaluationConfig.from_dict({"weights": {"safety": "4"}})


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


def test_a_full_tie_shares_a_rank_and_the_next_place_is_skipped():
    ranking = rank([result("c"), result("a"), result("b"), result("z", quality=0.1),
                    result("top", quality=0.9)])
    assert [(e.rank, e.result.name) for e in ranking.ranked] == [
        (1, "top"), (2, "a"), (2, "b"), (2, "c"), (5, "z")]


def test_a_name_never_decides_a_rank():
    for skills in ([result("a"), result("b")], [result("b"), result("a")]):
        assert [e.rank for e in rank(skills).ranked] == [1, 1]


def test_skills_that_differ_on_a_tie_break_do_not_share_a_rank():
    # Same overall and lift, as in test_ties_broken_by_lift_then_safety.
    ranking = rank([result("c", safety=0.8, quality=0.1), result("d")])
    assert [e.rank for e in ranking.ranked] == [1, 2]


def test_shared_rank_is_in_the_table():
    lines = table(rank([result("a"), result("b")])).splitlines()
    assert lines[2].startswith("| 1 | a |") and lines[3].startswith("| 1 | b |")


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
    assert entry.missing == [Dimension.CLARITY]


def test_unranked_skills_get_a_note_on_what_is_missing():
    skipped = DimensionStatus(Status.SKIPPED, detail="no API key")
    entry = rank([
        result("ranked", quality=0.4),
        result("partial", clarity=None, quality=0.9,
               dimensions={Dimension.CLARITY: skipped}),
    ]).unranked[0]
    assert entry.notes == ["Not ranked: no score for clarity (skipped: no API key)."]


@pytest.mark.parametrize("status, note", [
    (DimensionStatus(Status.SKIPPED), "clarity (skipped)"),  # a status, no detail
    (DimensionStatus(Status.ERROR, detail="​\x07"), "clarity (error)"),  # cleans to ""
])
def test_a_note_keeps_the_status_of_a_dimension_without_a_detail(status, note):
    entry = rank([result("a", clarity=None,
                         dimensions={Dimension.CLARITY: status})]).unranked[0]
    assert entry.notes == [f"Not ranked: no score for {note}."]


def test_the_scores_of_an_unranked_skill_are_kept_but_not_averaged():
    text = table(rank([result("partial", clarity=None, quality=0.9)]))
    assert "| - | partial | - | 0.90 | 0.50 | - |" in text
    assert "averages" not in text and "would place" not in text


def test_unranked_skills_are_ordered_by_name_not_by_their_scores():
    ranking = rank([result("y", clarity=None, quality=0.1),
                    result("x", clarity=None, quality=0.9),
                    result("z", **{d.value: None for d in Dimension})])
    assert [e.result.name for e in ranking.unranked] == ["x", "y", "z"]


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
    assert len(ranking.notes) == 1
    assert "partial for every skill with a safety score" in ranking.notes[0]
    assert "Check its safety notes" not in ranking.recommendation
    assert "- Safety is partial for every skill" in table(ranking)


def test_a_skill_without_safety_does_not_stop_the_all_partial_note():
    skipped = DimensionStatus(Status.SKIPPED)
    ranking = rank([result("a", dimensions={Dimension.SAFETY: PARTIAL}),
                    result("b", safety=None, dimensions={Dimension.SAFETY: skipped})])
    assert "every skill with a safety score" in ranking.notes[0]


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
    low = rank([result("a", safety=0.2)]).recommendation
    assert "safety score is low (0.20)" in low


def test_recommendation_warns_about_lift_of_zero_or_less():
    assert "Its lift is -0.60" in rank([result("a", lift=-0.6)]).recommendation
    assert "Its lift is +0.00" in rank([result("a", lift=0.0)]).recommendation
    assert "lift" not in rank([result("a", lift=0.2)]).recommendation


def test_a_tie_for_first_names_every_skill_and_picks_none():
    tied = rank([result("b", lift=0.2), result("a", lift=0.2), result("c", quality=0.1)])
    text = tied.recommendation
    assert text.startswith("Tied for highest overall (0.50) of 3 ranked skill(s): a, b.")
    assert "Use " not in text and "c" not in text.split(":")[1].split(".")[0]


def test_a_tie_for_first_keeps_the_warnings():
    assert "Their safety score is low (0.20)" in rank(
        [result("a", safety=0.2, lift=0.2), result("b", safety=0.2, lift=0.2)]).recommendation
    assert "Their lift is +0.00: on the tasks they do no better" in rank(
        [result("a"), result("b")]).recommendation


def test_a_critical_finding_on_either_tied_skill_is_warned_about():
    critical = Finding(dimension=Dimension.SAFETY, category="x", severity=Severity.CRITICAL,
                       source="own-checks", rule="r", quote="q", location="SKILL.md:1",
                       message="m")
    text = rank([result("a", findings=[critical]), result("b")]).recommendation
    assert "Check their safety notes before using them." in text


def test_no_winner_is_named_when_every_skill_is_incomplete():
    incomplete = rank([result("alpha", clarity=None, quality=1.0),
                       result("beta", safety=None, lift=-0.5)])
    assert not incomplete.ranked
    assert incomplete.recommendation.startswith("No complete overall comparison")
    assert "alpha" not in incomplete.recommendation
    assert "beta" not in incomplete.recommendation


def test_complete_skills_are_recommended_over_incomplete_ones():
    mixed = rank([result("low", quality=0.1), result("high", clarity=None, quality=1.0)])
    assert mixed.recommendation.startswith("Use low")


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


def test_markdown_that_could_fetch_or_run_is_escaped():
    image = "![](https://attacker.example/x)"
    html = "<img src=https://attacker.example/y>"
    finding = Finding(Dimension.SAFETY, "c", Severity.CRITICAL, "own-checks", "r", "q",
                      "[link](https://attacker.example/z)")
    text = table(rank([result(image, findings=[finding]), result(html, quality=0.1),
                       result("my_skill`code`\\", quality=0.0)]))
    assert r"!\[\](https://attacker.example/x)" in text  # name cell, note and recommendation
    assert "![](" not in text
    assert r"\<img src=https://attacker.example/y\>" in text
    assert not re.search(r"(?<!\\)[<\[]", text)  # no unescaped < or [ anywhere
    assert r"\[link\](https://attacker.example/z)" in text
    assert r"my_skill\`code\`\\" in text  # `_` is left alone; `\` cannot undo an escape


def test_escaping_leaves_the_notes_own_wording_alone():
    skipped = DimensionStatus(Status.SKIPPED, detail="no API key")
    ranking = rank([result("good", quality=0.9),
                    result("partial", clarity=None, dimensions={Dimension.CLARITY: skipped})])
    text = table(ranking)
    for note in ranking.unranked[0].notes:
        assert f"- partial: {note}" in text
    assert f"Recommendation: {ranking.recommendation}" in text


def test_a_name_with_nothing_left_once_cleaned():
    entry = rank([result("\x1b\x07 ​")]).ranked[0]
    assert entry.name == rank_module.UNNAMED
    assert "| 1 | (unnamed) |" in table(rank([result("\x1b")]))


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


def test_a_weight_zero_dimension_without_a_score_is_in_the_note():
    weights = {**{d: 1 for d in Dimension}, Dimension.CLARITY: 0}
    entry = rank([result("z", clarity=None)], weights).unranked[0]
    assert entry.missing == [Dimension.CLARITY]
    assert "clarity" in entry.notes[0]


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
    assert lines[3] == "| - | partial | - | 0.50 | 0.50 | - | 0.50 | 0.50 | 0.50 |"
    assert "Notes:" in lines
    assert lines[-1].startswith("Recommendation: Use good")
