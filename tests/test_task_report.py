"""The task section of ranked-evaluation.md (MET-49): built from SkillResult.tests alone."""

import re

from metaskill.evaluate import rank as rank_module
from metaskill.evaluate.judge import JudgeType
from metaskill.evaluate.models import (
    Dimension, EvaluationConfig, Metrics, Scores, SkillResult, TestResult, Variant)

CONFIG = EvaluationConfig(runs_per_task=3)


def run(task, variant, n, passed, *, tokens=100, calls=2, tools=1, reason="ok"):
    return TestResult(
        task_id=task, description="d", variant=variant, run=n, judge=JudgeType.DETERMINISTIC,
        passed=passed, score=1.0 if passed else 0.0, reason=reason,
        metrics=Metrics(input_tokens=tokens, output_tokens=0, llm_calls=calls, tool_calls=tools),
        output="out")


def render(tests, name="skill-a"):
    found = {d.value: 0.5 for d in Dimension}
    result = SkillResult(name, Scores(overall=None, **found), 0.0, tests=tests)
    ranking = rank_module.rank([result], CONFIG)
    meta = CONFIG.to_meta("t", [name], "", "2")
    return rank_module.markdown(meta, ranking)


def section(text):
    return text.split("### Task results", 1)[1].split("\n### ", 1)[0]


def test_a_report_without_tasks_has_no_task_section():
    assert "Task results" not in render([]) and "Logs:" not in render([])


def test_each_task_and_variant_has_a_row_with_pass_rate_and_mean_cost():
    tests = [run("t1", Variant.WITH_SKILL, 1, True, tokens=100, calls=2, tools=1),
             run("t1", Variant.WITH_SKILL, 2, True, tokens=200, calls=4, tools=3),
             run("t1", Variant.WITH_SKILL, 3, False, tokens=300, calls=6, tools=5),
             run("t1", Variant.BASELINE, 1, False, tokens=1000, calls=10, tools=7),
             run("t1", Variant.BASELINE, 2, False, tokens=2000, calls=20, tools=9),
             run("t1", Variant.BASELINE, 3, False, tokens=3000, calls=30, tools=11)]
    rows = section(render(tests)).splitlines()
    assert "| t1 | with_skill | 2/3 (67%) | 200 | 4.0 | 3.0 | improved |" in rows
    assert "| t1 | baseline | 0/3 (0%) | 2000 | 20.0 | 9.0 | - |" in rows
    assert rows.index("| t1 | with_skill | 2/3 (67%) | 200 | 4.0 | 3.0 | improved |") < \
        rows.index("| t1 | baseline | 0/3 (0%) | 2000 | 20.0 | 9.0 | - |")


def test_tool_calls_that_were_not_measured_show_a_question_mark():
    tests = [run("t", Variant.WITH_SKILL, n, True, tools=None) for n in (1, 2, 3)]
    assert "| t | with_skill | 3/3 (100%) | 100 | 2.0 | ? | incomplete |" in section(render(tests))


def test_failed_runs_are_listed_with_their_reasons_and_capped():
    tests = [run("t", Variant.WITH_SKILL, n, False, reason=f"reason {n}") for n in (1, 2, 3)]
    text = section(render(tests))
    assert "- t, with_skill r2: reason 2" in text
    many = [run(f"t{i}", Variant.WITH_SKILL, 1, False, reason=f"why {i}") for i in range(14)]
    text = section(render(many))
    assert text.count("\n- t") == rank_module.MAX_LISTED_FAILURES
    assert "and 4 more failed run(s)" in text


def test_runs_that_could_not_be_made_are_noted_and_the_logs_are_pointed_to():
    # Three runs planned per variant: with_skill made 2, baseline made none.
    tests = [run("t", Variant.WITH_SKILL, 1, True), run("t", Variant.WITH_SKILL, 2, True),
             run("u", Variant.WITH_SKILL, 1, True), run("u", Variant.WITH_SKILL, 2, True),
             run("u", Variant.WITH_SKILL, 3, True), run("t", Variant.WITH_SKILL, 3, True),
             run("u", Variant.BASELINE, 1, True), run("u", Variant.BASELINE, 2, True),
             run("u", Variant.BASELINE, 3, True)]
    text = section(render(tests))
    assert "3 run(s) could not be made and are not counted above" in text  # t's baseline
    assert "Logs: skill-a/logs/ in this report's folder" in text


def test_a_complete_run_notes_nothing_missing():
    tests = [run("t", v, n, True) for v in Variant for n in (1, 2, 3)]
    assert "could not be made" not in section(render(tests))


def test_text_from_a_task_or_a_run_cannot_inject_markdown_or_break_the_table():
    evil = "![x](https://attacker.example/?q=1) <img src=x> | injected |\n# heading"
    tests = [run(evil, Variant.WITH_SKILL, 1, False, reason=evil),
             run(evil, Variant.BASELINE, 1, False, reason=evil)]
    text = section(render(tests, name=evil))
    assert not re.search(r"(?<!\\)[<\[`]", text)  # nothing unescaped that could render
    assert "![" not in text.replace("!\\[", "")
    assert "\n# heading" not in text
    for row in (line for line in text.splitlines() if line.startswith("| ") and "with_skill" in line):
        assert len(re.split(r"(?<!\\)\|", row.strip("|"))) == 7  # a `|` in the id stays in its cell


def test_each_task_says_whether_the_skill_beat_the_baseline():
    def labels(with_skill, baseline):
        tests = [run("t", Variant.WITH_SKILL, i + 1, p) for i, p in enumerate(with_skill)]
        tests += [run("t", Variant.BASELINE, i + 1, p) for i, p in enumerate(baseline)]
        row = next(r for r in section(render(tests)).splitlines() if "| with_skill |" in r)
        return row.rsplit("|", 2)[1].strip()

    assert labels([True, True], [True, False]) == "improved"
    assert labels([True, False], [True, True]) == "regressed"
    assert labels([True, False], [False, True]) == "unchanged"
    assert labels([False, False], [False, False]) == "unchanged"
    # Rates are compared, not counts: 1 of 2 against 1 of 3 is an improvement.
    assert labels([True, False], [True, False, False]) == "improved"


def test_a_task_with_only_one_variant_is_incomplete():
    only_baseline = [run("t", Variant.BASELINE, 1, True)]
    only_skill = [run("t", Variant.WITH_SKILL, 1, True)]
    assert "| t | baseline | 1/1 (100%) | 100 | 2.0 | 1.0 | incomplete |" in section(render(only_baseline))
    assert "| t | with_skill | 1/1 (100%) | 100 | 2.0 | 1.0 | incomplete |" in section(render(only_skill))
