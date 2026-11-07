"""evaluate_skill and rank_skills, end to end on skills written to a temporary folder.

SKILL.md files here have no frontmatter, so these tests do not need Hermes' YAML loader.
"""

import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import metaskill.evaluate as evaluate
from metaskill.evaluate import evaluate_skill, rank_skills
from metaskill.evaluate.models import (
    Dimension,
    DimensionStatus,
    EvaluationConfig,
    Finding,
    Report,
    Scores,
    Severity,
    SkillResult,
    Status,
)
from metaskill.evaluate.rank import markdown, rank, table
from metaskill.evaluate.sandbox import DockerSandbox

RISKY = "Run `rm -rf ~/Documents/drafts` to clean up."
NOT_BUILT = [d for d in Dimension if d is not Dimension.SAFETY]


def make_skill(root, name, body="# Skill\n\nSummarize the file.\n"):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(body, encoding="utf-8")
    return folder


def load(result):
    """The Report in a result's JSON file."""
    path = next(p for p in result["artifacts"] if p.endswith(".json"))
    return Report.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


@pytest.fixture
def out(tmp_path):
    return tmp_path / "reports"


@pytest.fixture
def scorers(monkeypatch, tmp_path):
    """Puts a module in metaskill.evaluate for one test: scorers("clarity", source)."""
    folder = tmp_path / "scorers"
    folder.mkdir()
    monkeypatch.setattr(evaluate, "__path__", [*evaluate.__path__, str(folder)])
    added = []

    def add(module, source):
        (folder / f"{module}.py").write_text(source, encoding="utf-8")
        added.append(f"{evaluate.__name__}.{module}")
        importlib.invalidate_caches()

    yield add
    for name in added:
        sys.modules.pop(name, None)


# --- Scoring ---------------------------------------------------------------


def test_safety_is_scored_and_unbuilt_dimensions_are_null(tmp_path, out):
    report = load(evaluate_skill(make_skill(tmp_path, "a"), output_path=out))
    [skill] = report.skills
    assert skill.scores.safety is not None and skill.scores.overall is None
    for dimension in NOT_BUILT:
        assert getattr(skill.scores, dimension.value) is None
        assert skill.dimensions[dimension].status is Status.SKIPPED
        assert skill.dimensions[dimension].detail == "not built yet"


def test_no_tasks_means_no_lift(tmp_path, out):
    assert load(evaluate_skill(make_skill(tmp_path, "a"), output_path=out)).skills[0].lift is None


def test_a_skill_with_unbuilt_dimensions_is_listed_as_unranked(tmp_path, out):
    result = evaluate_skill(make_skill(tmp_path, "a"), output_path=out)
    assert result["counts"] == {"skills": 1, "ranked": 0, "unranked": 1}
    assert "| - | a | - |" in result["table"]
    assert "no skill is recommended" in result["table"]
    assert any(w.startswith("Partial result") for w in result["warnings"])


def test_a_new_dimension_module_is_picked_up(tmp_path, out, scorers):
    scorers("clarity", "from metaskill.evaluate.models import *\n"
                       "def score(skill, config, llm=None):\n"
                       "    return DimensionScore(Dimension.CLARITY, 0.75, reason=str(llm))\n")
    ctx = SimpleNamespace(llm="the-llm")
    skill = load(evaluate_skill(make_skill(tmp_path, "a"), output_path=out, ctx=ctx)).skills[0]
    assert skill.scores.clarity == 0.75
    assert skill.dimensions[Dimension.CLARITY].reason == "the-llm"  # ctx.llm reached it


def test_a_module_that_fails_to_import_is_an_error_not_a_skip(tmp_path, out, scorers):
    scorers("quality", "import a_module_that_is_not_there\n")
    skill = load(evaluate_skill(make_skill(tmp_path, "a"), output_path=out)).skills[0]
    status = skill.dimensions[Dimension.QUALITY]
    assert status.status is Status.ERROR and status.detail == "failed: ModuleNotFoundError"
    assert skill.scores.quality is None


def test_a_scorer_that_raises_is_an_error_without_its_message(tmp_path, out, scorers):
    scorers("clarity", "def score(skill, config, llm=None):\n"
                       "    raise RuntimeError('leaky-message-123')\n")
    result = evaluate_skill(make_skill(tmp_path, "a"), output_path=out)
    assert load(result).skills[0].dimensions[Dimension.CLARITY].status is Status.ERROR
    assert "leaky-message-123" not in json.dumps(result)


def test_no_judge_is_a_skipped_dimension(tmp_path, out, scorers):
    scorers("clarity", "from metaskill.evaluate.judge import JudgeUnavailable\n"
                       "def score(skill, config, llm=None):\n"
                       "    raise JudgeUnavailable('no provider')\n")
    status = load(evaluate_skill(make_skill(tmp_path, "a"), output_path=out)
                  ).skills[0].dimensions[Dimension.CLARITY]
    assert status.status is Status.SKIPPED and status.detail == "no judge model is available"


def test_docker_is_not_needed(tmp_path, out, monkeypatch):
    def fail(self):
        raise AssertionError("Docker was asked about")

    monkeypatch.setattr(DockerSandbox, "check", fail)
    result = evaluate_skill(make_skill(tmp_path, "a"), output_path=out)
    assert all(d["backend"] != "sandbox" for d in result["dependencies"])


def test_what_is_missing_is_listed(tmp_path, out, monkeypatch):
    monkeypatch.setattr("metaskill.evaluate.safety.CiscoBackend.available", lambda self: False)
    result = evaluate_skill(make_skill(tmp_path, "a"), output_path=out)
    assert [d["package"] for d in result["dependencies"]] == ["cisco-ai-skill-scanner"]
    assert result["dependencies"][0]["required"] is False


# --- Paths and failures ----------------------------------------------------


def test_a_skill_md_path_is_the_skill_in_its_folder(tmp_path, out):
    folder = make_skill(tmp_path, "a")
    assert load(evaluate_skill(folder / "SKILL.md", output_path=out)).skills[0].name == "a"


def test_rank_scores_every_skill_in_one_report(tmp_path, out):
    report = load(rank_skills([make_skill(tmp_path, "b"), make_skill(tmp_path, "a")],
                              output_path=out))
    assert report.meta.skills == ["b", "a"]  # input order (evaluate.md 2.1)
    assert [s.name for s in report.skills] == ["a", "b"]  # unranked: by name


def test_one_bad_skill_does_not_stop_the_others(tmp_path, out):
    bad = tmp_path / "empty"
    bad.mkdir()
    result = rank_skills([bad, make_skill(tmp_path, "good")], output_path=out)
    assert [s.name for s in load(result).skills] == ["good"]
    assert any(f"{bad} could not be evaluated: FileNotFoundError" == w
               for w in result["warnings"])


def test_no_skill_that_can_be_evaluated_is_an_error(tmp_path, out):
    (tmp_path / "empty").mkdir()
    result = evaluate_skill(tmp_path / "empty", output_path=out)
    assert result["status"] == "error" and "artifacts" not in result
    assert not out.exists()


# --- Config ----------------------------------------------------------------


def test_config_file_is_used(tmp_path, out):
    settings = {"runs_per_task": 5, "output_dir": str(out / "from-config")}
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(settings), encoding="utf-8")
    result = evaluate_skill(make_skill(tmp_path, "a"), config_path=path)
    assert load(result).meta.runs_per_task == 5
    assert (out / "from-config") in Path(result["artifacts"][0]).parents


def test_output_path_wins_over_the_config(tmp_path, out):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"output_dir": str(tmp_path / "other")}), encoding="utf-8")
    result = evaluate_skill(make_skill(tmp_path, "a"), config_path=path, output_path=out)
    assert out in Path(result["artifacts"][0]).parents


@pytest.mark.parametrize("text", ["not json", "[1]", '{"runs_per_task": 0}',
                                  '{"weights": {"safety": "4"}}', '{"weights": 1}'])
def test_a_bad_config_is_an_error_that_does_not_echo_it(tmp_path, out, text):
    path = tmp_path / "settings.json"
    path.write_text(text, encoding="utf-8")
    result = evaluate_skill(make_skill(tmp_path, "a"), config_path=path, output_path=out)
    assert result == {"status": "error", "error": "config_path is not a valid evaluation config"}


# --- Reports ---------------------------------------------------------------


def test_each_call_writes_a_new_folder_with_both_reports(tmp_path, out):
    folder = make_skill(tmp_path, "a")
    first = evaluate_skill(folder, output_path=out)
    second = evaluate_skill(folder, output_path=out)  # very likely the same second
    folders = {Path(p).parent for p in first["artifacts"] + second["artifacts"]}
    assert len(folders) == 2
    for run in folders:
        assert sorted(p.name for p in run.iterdir()) == ["ranked-evaluation.json",
                                                         "ranked-evaluation.md"]
        assert run.name == load({"artifacts": [str(run / "ranked-evaluation.json")]}
                                ).meta.timestamp


def test_meta_says_no_judge_ran_even_if_one_was_asked_for(tmp_path, out):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"judge_model": "asked-for"}), encoding="utf-8")
    meta = load(evaluate_skill(make_skill(tmp_path, "a"), config_path=path,
                               output_path=out)).meta
    assert meta.judge_model == "" and meta.checks_version


def test_findings_stay_in_the_reports_and_out_of_the_response(tmp_path, out):
    result = evaluate_skill(make_skill(tmp_path, "a", f"# Skill\n\n{RISKY}\n"), output_path=out)
    [finding] = [f for f in load(result).skills[0].findings if "rm -rf" in f.quote]
    readable = Path(result["artifacts"][1]).read_text(encoding="utf-8")
    assert finding.rule in readable and "rm -rf" in readable
    assert "rm -rf" not in json.dumps(result) and finding.rule not in json.dumps(result)


def test_local_warnings_stay_out_of_the_response(tmp_path, out, monkeypatch):
    home = tmp_path / "home"
    (home / "Documents" / "drafts").mkdir(parents=True)
    for i in range(30):
        (home / "Documents" / "drafts" / f"{i}.txt").write_text("x", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    result = evaluate_skill(make_skill(tmp_path, "a", f"# Skill\n\n{RISKY}\n"), output_path=out)
    assert load(result).skills[0].local_warnings
    assert str(home) not in json.dumps(result).replace("\\\\", "\\")
    assert "remove before sharing" in Path(result["artifacts"][1]).read_text(encoding="utf-8")


def skill_result(**kwargs):
    found = {d.value: None for d in Dimension}
    return SkillResult("a", Scores(overall=None, **found), None, **kwargs)


def test_markdown_escapes_what_came_from_the_skill():
    link = "![x](https://attacker.example/?q=1) <img src=x>"
    finding = Finding(Dimension.SAFETY, link, Severity.HIGH, link, link, link, link, link)
    status = DimensionStatus(Status.ERROR, reason=link, detail=link)
    ranking = rank([skill_result(findings=[finding], dimensions={Dimension.SAFETY: status})],
                   EvaluationConfig())
    meta = EvaluationConfig().to_meta("t", [link], link, "2")
    text = markdown(meta, ranking)
    assert "![x](" not in text.replace("!\\[x\\](", "") and "<img" not in text.replace("\\<img", "")
    assert text.count("!\\[x\\]") >= 8


def test_markdown_has_the_table_and_says_lift_was_not_measured():
    ranking = rank([skill_result()], EvaluationConfig())
    text = markdown(EvaluationConfig().to_meta("t", ["a"], "", "2"), ranking)
    assert table(ranking) in text
    assert "Lift: not measured" in text and "Scanner version: not run" in text


# --- lift can be None ------------------------------------------------------


def test_lift_none_round_trips_and_out_of_range_is_still_refused():
    result = skill_result()
    assert SkillResult.from_dict(json.loads(json.dumps(result.to_dict()))).lift is None
    with pytest.raises(ValueError):
        SkillResult("a", result.scores, 1.5)


def test_a_ranked_skill_without_lift_gets_no_lift_warning():
    scores = Scores(overall=None, **{d.value: 0.5 for d in Dimension})
    text = rank([SkillResult("a", scores, None)], EvaluationConfig()).recommendation
    assert text.startswith("Use a:") and "lift" not in text


# --- Loading ---------------------------------------------------------------


def test_importing_this_package_does_not_import_safety():
    """The plugin imports judge.py through it, so a failure in safety must not get here."""
    code = ("import sys, metaskill.evaluate.judge; "
            "sys.exit('metaskill.evaluate.safety' in sys.modules)")
    root = Path(__file__).resolve().parent.parent
    assert subprocess.run([sys.executable, "-c", code], cwd=root).returncode == 0
