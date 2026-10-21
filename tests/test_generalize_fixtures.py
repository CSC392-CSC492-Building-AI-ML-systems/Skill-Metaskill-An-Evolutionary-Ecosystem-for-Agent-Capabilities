"""The deterministic detector run end to end over the skills in tests/skill_fixtures."""

import json

from skill_fixtures import Row, materialize
from skill_fixtures.secrets import assert_not_leaked

from metaskill.generalize.detect import Category, scan_skill


def rows(result) -> list[Row]:
    return [Row(f.file, f.line, f.category, f.rule, f.confidence, f.action, f.name, f.value)
            for f in result.findings]


def snapshot(root):
    return {p.relative_to(root.parent).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in sorted(root.parent.rglob("*")) if p.is_file() and not p.is_symlink()}


def test_expected_findings(skill_fixture, skill_dir):
    assert rows(scan_skill(skill_dir)) == skill_fixture.expected


def test_locations_point_at_the_value(skill_fixture, skill_dir):
    result = scan_skill(skill_dir)
    for finding in result.findings:
        text = (skill_dir / finding.file).read_bytes().decode("utf-8")
        span = text[finding.start:finding.end]
        line_start = text.rfind("\n", 0, finding.start) + 1
        assert text.count("\n", 0, finding.start) + 1 == finding.line, finding.id
        assert finding.start - line_start + 1 == finding.column, finding.id
        if finding.sensitive:
            assert any(span in secret or secret in span for secret in skill_fixture.secrets), \
                f"{finding.id}: credential span is not one of the fixture's secrets"
        else:
            assert span == finding.value, finding.id


def test_skipped_files(skill_fixture, skill_dir):
    skipped = {s.path: s.reason for s in scan_skill(skill_dir).skipped}
    assert skipped == skill_fixture.skipped


def test_repeated_scans_are_identical(skill_fixture, skill_dir, tmp_path):
    first, second = scan_skill(skill_dir), scan_skill(skill_dir)
    assert first.to_dict() == second.to_dict()
    elsewhere = materialize(skill_fixture, tmp_path / "copy")
    assert [f.to_dict() for f in scan_skill(elsewhere).findings] == \
        [f.to_dict() for f in first.findings]


def test_ids_are_unique(skill_dir):
    ids = [f.id for f in scan_skill(skill_dir).findings]
    assert len(ids) == len(set(ids))


def test_findings_do_not_overlap(skill_dir):
    findings = scan_skill(skill_dir).findings
    for a, b in zip(findings, findings[1:]):
        if a.file == b.file:
            assert a.end <= b.start, (a.id, b.id)


def test_secrets_never_leak(skill_fixture, skill_dir):
    result = scan_skill(skill_dir)
    assert_not_leaked(skill_fixture.secrets, json.dumps(result.to_dict()),
                      *(repr(f) for f in result.findings), repr(result))
    for finding in result.findings:
        if finding.category is Category.CREDENTIAL:
            assert finding.sensitive


def test_scanning_leaves_files_unchanged(skill_dir):
    before = snapshot(skill_dir)
    scan_skill(skill_dir)
    assert snapshot(skill_dir) == before
