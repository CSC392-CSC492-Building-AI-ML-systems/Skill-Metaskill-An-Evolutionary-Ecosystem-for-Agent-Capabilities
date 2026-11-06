import os
import time
from pathlib import Path

import pytest

from metaskill.evaluate import safety
from metaskill.evaluate.models import LocalWarning, Severity, Skill, SkillResult, Scores
from metaskill.evaluate.safety import local
from metaskill.evaluate.safety.checks import DeleteTarget

SKILL = Skill(path=Path("cleanup-skill"), name="cleanup-skill", body="")


def target(path, follows_links=True):
    return DeleteTarget(path, "SKILL.md:14", f"rm -rf {path}", follows_links, "unknown", Severity.HIGH)


def files(folder, count, old=False):
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        f = folder / f"f{i}.txt"
        f.write_text("x")
        if old:
            os.utime(f, (time.time() - 30 * 86400,) * 2)


def symlink(target, link):
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("cannot create symlinks here")


def warn(home, *targets, env=None, config=None):
    return local.local_warnings(SKILL, list(targets), config, env=env or {}, home=home)


# --- Which targets are looked up ------------------------------------------------------


def test_home_path_with_many_files(tmp_path):
    files(tmp_path / "drafts", 312, old=True)
    [w] = warn(tmp_path, target("~/drafts"))
    assert w.resolved == str(tmp_path / "drafts")
    assert w.reasons == ["contains 312 files"]
    assert w.location == "SKILL.md:14"
    assert w.message == ("`cleanup-skill` deletes `~/drafts` without asking (SKILL.md:14). "
                         "On this machine it contains 312 files.")


def test_absolute_path(tmp_path):
    files(tmp_path / "data", 150, old=True)
    assert len(warn(tmp_path, target(str(tmp_path / "data") + "/"))) == 1


def test_environment_variable_is_resolved(tmp_path):
    files(tmp_path / "out", 150, old=True)
    assert len(warn(tmp_path, target("$OUTPUT_DIR"), env={"OUTPUT_DIR": str(tmp_path / "out")})) == 1


def test_unset_variable_is_skipped(tmp_path):
    assert warn(tmp_path, target("$OUTPUT_DIR")) == []


def test_placeholder_is_resolved_from_the_users_config(tmp_path):
    files(tmp_path / "out", 150, old=True)
    config = {"OUTPUT_DIR": str(tmp_path / "out")}
    assert len(warn(tmp_path, target("{{OUTPUT_DIR}}/"), config=config)) == 1
    assert warn(tmp_path, target("{{OUTPUT_DIR}}/")) == []


def test_relative_path_is_skipped(tmp_path, monkeypatch):
    files(tmp_path / "data", 150, old=True)
    monkeypatch.chdir(tmp_path)
    assert warn(tmp_path, target("data/")) == []


def test_missing_target_has_no_warning(tmp_path):
    assert warn(tmp_path, target("~/does-not-exist")) == []


# --- What counts as important ---------------------------------------------------------


def test_small_old_folder_has_no_warning(tmp_path):
    files(tmp_path / "scratch", 5, old=True)
    assert warn(tmp_path, target("~/scratch")) == []


def test_home_folder(tmp_path):
    [w] = warn(tmp_path, target("~"))
    assert "is your home folder" in w.reasons


def test_top_level_home_folder(tmp_path):
    (tmp_path / "Documents").mkdir()
    [w] = warn(tmp_path, target("$HOME/Documents"))
    assert w.reasons == ["is your Documents folder"]


def test_git_repository(tmp_path):
    files(tmp_path / "app", 3, old=True)
    (tmp_path / "app" / ".git").mkdir()
    [w] = warn(tmp_path, target("~/app"))
    assert w.reasons == ["is or contains a git repository"]


def test_recently_changed_files(tmp_path):
    files(tmp_path / "notes", 3)
    [w] = warn(tmp_path, target("~/notes"))
    assert w.reasons == ["has files changed in the last 7 days"]


def test_symlink_followed_by_the_delete(tmp_path):
    (tmp_path / "thesis").mkdir()
    symlink(tmp_path / "thesis", tmp_path / "build")
    [w] = warn(tmp_path, target("~/build/", follows_links=True))
    assert w.reasons[0].startswith(f"is a link to `{tmp_path / 'thesis'}`")


def test_symlink_not_followed_removes_only_the_link(tmp_path):
    (tmp_path / "thesis").mkdir()
    symlink(tmp_path / "thesis", tmp_path / "build")
    assert warn(tmp_path, target("~/build", follows_links=False)) == []


def test_symlinks_are_not_followed_while_counting(tmp_path):
    files(tmp_path / "elsewhere", 500, old=True)
    (tmp_path / "small").mkdir()
    symlink(tmp_path / "elsewhere", tmp_path / "small" / "link")
    assert warn(tmp_path, target("~/small")) == []


def test_counting_stops_at_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(local, "MAX_ENTRIES", 50)
    files(tmp_path / "big", 80, old=True)
    [w] = warn(tmp_path, target("~/big"))
    assert w.reasons == ["contains at least 50 files"]


def test_nothing_is_changed(tmp_path):
    files(tmp_path / "Documents" / "drafts", 120)
    (tmp_path / "Documents" / "drafts" / ".git").mkdir()

    def snapshot():
        return {(str(p), p.stat().st_size, p.stat().st_mtime) for p in tmp_path.rglob("*")}

    before = snapshot()
    assert warn(tmp_path, target("~/Documents"), target("~/Documents/drafts"))
    assert snapshot() == before


# --- Entry point and report --------------------------------------------------------------


def test_safety_local_warnings_reads_the_skills_deletes(tmp_path, monkeypatch):
    home = tmp_path / "home"
    files(home / "Documents" / "drafts", 120, old=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Path.home() on Windows
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    body = "```bash\nrm -rf ~/Documents/drafts\n```\n"
    (skill_dir / "SKILL.md").write_text(body)
    [w] = safety.local_warnings(Skill(path=skill_dir, name="cleanup", body=body))
    assert w.location == "SKILL.md:2"
    assert w.reasons == ["contains 120 files"]


def test_local_warnings_round_trip_in_the_report():
    w = LocalWarning("~/x", "/home/u/x", "SKILL.md:1", ["contains 120 files"], "msg")
    result = SkillResult("s", Scores(None, None, None, None, None, None, None), 0.0, local_warnings=[w])
    assert SkillResult.from_dict(result.to_dict()).local_warnings == [w]



# --- Windows forms ----------------------------------------------------------------------
# These check how targets are resolved; the files themselves can only be looked up on Windows.


WIN_HOME = r"C:\Users\alice"


def test_windows_home_variables_resolve_to_the_home_folder():
    for target in (r"%USERPROFILE%\Documents", r"$env:USERPROFILE\Documents", r"${env:USERPROFILE}\Documents"):
        assert str(local._resolve(target, {}, {}, WIN_HOME)) == r"C:\Users\alice\Documents"


def test_windows_environment_variables_match_in_any_case():
    env = {"AppData": r"C:\Users\alice\AppData\Roaming"}
    assert str(local._resolve(r"$env:APPDATA\myapp\*", {}, env, WIN_HOME)) == r"C:\Users\alice\AppData\Roaming\myapp"
    assert str(local._resolve(r"%appdata%\myapp", {}, env, WIN_HOME)) == r"C:\Users\alice\AppData\Roaming\myapp"


def test_windows_drive_paths_and_tilde():
    assert str(local._resolve("C:/Data/../Data/x", {}, {}, WIN_HOME)) == r"C:\Data\x"
    assert str(local._resolve(r"~\notes", {}, {}, WIN_HOME)) == r"C:\Users\alice\notes"


def test_unresolved_or_relative_windows_targets_are_skipped():
    assert local._resolve(r"%OUT_DIR%\build", {}, {}, WIN_HOME) is None
    assert local._resolve(r".\dist", {}, {}, WIN_HOME) is None
    assert local._resolve(r"build\*", {}, {}, WIN_HOME) is None


def test_system_folder_reason_uses_the_shared_check(tmp_path):
    # Own home is reported as home, not as a system folder.
    [w] = warn(tmp_path, target("~"))
    assert w.reasons == ["is your home folder"]
