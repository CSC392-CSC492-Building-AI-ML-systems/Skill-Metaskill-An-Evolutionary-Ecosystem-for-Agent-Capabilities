"""Symlinks and Windows junctions are not followed (metaskill/links.py).

Junctions exist only on Windows, so the tests that make real ones run there; the others
stand one in by telling links.py that a folder is a junction.
"""

import os
import subprocess
import sys
import tarfile

import pytest

from metaskill import links
from metaskill.evaluate.config_evidence import collect_configuration_evidence
from metaskill.evaluate.models import SafetyTier, Severity, Skill
from metaskill.evaluate.safety.checks import ChecksBackend
from metaskill.evaluate.safety.cisco import _without_links
from metaskill.evaluate.safety.local import _reasons
from metaskill.evaluate.sandbox import _keep

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions")
OUTSIDE = "contact: dana.reyes@outside-private.com\nUse {{config.outside_only}}\n"


def make_skill(root):
    skill = root / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    return skill


def load(path):
    """Like Skill.load(), listing the files as a walk finds them, junctions included."""
    files = sorted((file.relative_to(path) for folder, _, names in os.walk(path)
                    for file in [path.__class__(folder) / n for n in names]
                    if file.name != "SKILL.md"), key=str)
    return Skill(path=path, name=path.name, body="# Skill\n", files=files)


def fill(folder):
    folder.mkdir(exist_ok=True)
    (folder / "secret.txt").write_text(OUTSIDE, encoding="utf-8")
    (folder / "notes.md").write_text(OUTSIDE, encoding="utf-8")
    (folder / "wipe.sh").write_text("rm -rf ~/Documents\n", encoding="utf-8")


def rules(skill):
    return sorted((f.rule, f.location) for f in
                  ChecksBackend().scan(load(skill), SafetyTier.STATIC).findings)


# --- Any system: a folder stood in for a junction ------------------------------------------


@pytest.fixture
def pretend_junction(tmp_path, monkeypatch):
    skill = make_skill(tmp_path)
    fill(skill / "ref")
    monkeypatch.setattr(links, "_isjunction", lambda p: os.path.basename(p) == "ref")
    return skill


def test_a_junction_and_what_is_under_it_go_through_a_link(pretend_junction):
    skill = pretend_junction
    assert links.is_link(skill / "ref") and not links.is_link(skill / "SKILL.md")
    assert links.through_link(skill, skill / "ref" / "secret.txt")
    assert not links.through_link(skill, skill / "SKILL.md")
    assert not links.through_link(skill, skill / "not-there.txt")


def test_a_path_outside_the_folder_goes_through_a_link(tmp_path):
    skill = make_skill(tmp_path)
    assert links.through_link(skill, tmp_path / "elsewhere")
    assert links.through_link(skill, skill / ".." / "elsewhere")


def test_a_folder_reached_through_a_link_is_still_read(tmp_path):
    """A skill installed as a link to its folder, as in ~/.hermes/skills."""
    skill = make_skill(tmp_path)
    try:
        os.symlink(skill, tmp_path / "installed", target_is_directory=True)
    except OSError:
        pytest.skip("cannot create symlinks here")
    installed = tmp_path / "installed"
    assert not links.through_link(installed, installed / "SKILL.md")


def test_every_reader_leaves_a_junction_alone(pretend_junction, tmp_path):
    skill = pretend_junction
    dest, left_out = _without_links(skill, tmp_path / "copy")
    assert left_out == ["ref"] and not (dest / "ref").exists()
    assert rules(skill) == []
    assert collect_configuration_evidence(load(skill)).findings == []
    [reason] = _reasons(skill / "ref", False, tmp_path, 0) or ["only the link goes"]
    assert reason == "only the link goes"


# --- Windows: real junctions ----------------------------------------------------------------


@pytest.fixture
def junction(tmp_path):
    """A skill with `ref`, a junction to a folder outside it."""
    skill, outside = make_skill(tmp_path), tmp_path / "outside"
    fill(outside)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(skill / "ref"), str(outside)], check=True,
                   capture_output=True)
    yield skill, outside
    os.rmdir(skill / "ref")  # the junction only, so cleanup never reaches through it


@windows_only
def test_a_real_junction_is_a_link(junction):
    skill, _ = junction
    assert not (skill / "ref").is_symlink() and links.is_link(skill / "ref")
    assert links.through_link(skill, skill / "ref" / "secret.txt")


@windows_only
def test_a_junction_is_not_copied_for_cisco(junction, tmp_path):
    dest, left_out = _without_links(junction[0], tmp_path / "copy")
    assert left_out == ["ref"] and not (dest / "ref").exists()


@windows_only
def test_a_junction_out_of_the_skill_is_reported_and_not_read(junction):
    skill, _ = junction
    assert rules(skill) == [("bundle-symlink", "ref")]
    [finding] = ChecksBackend().scan(load(skill), SafetyTier.STATIC).findings
    assert finding.severity is Severity.MEDIUM and "outside" in finding.quote
    assert "dana.reyes" not in finding.quote


@windows_only
def test_configuration_evidence_does_not_read_through_a_junction(junction):
    assert collect_configuration_evidence(load(junction[0])).findings == []


@windows_only
def test_local_warnings_treat_a_junction_as_a_link(junction, tmp_path):
    skill, outside = junction
    assert _reasons(skill / "ref", False, tmp_path, 0) == []
    [reason] = _reasons(skill / "ref", True, tmp_path, 0)
    assert reason.startswith("is a link to") and str(outside.name) in reason


# --- Sandbox export: link targets ----------------------------------------------------------


def link_member(target, kind=tarfile.SYMTYPE):
    member = tarfile.TarInfo("link")
    member.type, member.linkname = kind, target
    return member


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE])
@pytest.mark.parametrize("target", ["C:", "C:..", "C:x", "c:\\x", "D:sub/file", "a:b", "x/c:y"])
def test_a_link_target_with_a_drive_or_colon_is_refused(tmp_path, kind, target):
    assert _keep(link_member(target, kind), str(tmp_path)) is None


@pytest.mark.parametrize("target", ["file", "sub/file"])
def test_a_link_that_goes_down_is_kept(tmp_path, target):
    assert _keep(link_member(target), str(tmp_path)) is not None
