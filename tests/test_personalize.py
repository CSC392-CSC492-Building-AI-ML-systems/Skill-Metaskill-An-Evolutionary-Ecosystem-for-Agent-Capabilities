"""personalize_skill(): a generalized skill plus a config restores values, and bad input is refused."""

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from metaskill.generalize.config import ConfigField, build_config_template
from metaskill.generalize.detect import Category
from metaskill.personalize import REPORT_FILE, SKILL_DIR, personalize_skill
from metaskill.personalize.config import PersonalizeError, parse_schema, validate_config
from metaskill.personalize.transform import substitute
from skill_fixtures import secrets as fake_secrets

FIXTURE = Path(__file__).parent / "fixtures" / "personalize" / "deploy-helper"
FIELDS = {
    "build_host": Category.ORGANIZATION_HOST,
    "contact_email": Category.EMAIL,
    "repository_url": Category.REPOSITORY,
    "workdir": Category.FILE_PATH,
}


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def write_json(path: Path, data) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def make_bundle(base: Path, files: dict[str, str | bytes], fields: dict[str, Category]) -> Path:
    """A generalize output directory with the given template files and schema fields."""
    bundle = base / "bundle"
    for rel, content in files.items():
        path = bundle / "generalized-skill" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    config_fields = {name: ConfigField(name, category, category is Category.CREDENTIAL)
                     for name, category in fields.items()}
    write_json(bundle / "config.schema.json", build_config_template(config_fields)[0])
    return bundle


@pytest.fixture
def bundle(tmp_path):
    return Path(shutil.copytree(FIXTURE / "generalized", tmp_path / "bundle"))


@pytest.fixture
def values():
    return json.loads((FIXTURE / "config.json").read_text(encoding="utf-8"))


def run(bundle, tmp_path, config):
    out = tmp_path / "out"
    return personalize_skill(bundle, write_json(tmp_path / "config.json", config), out), out


# The hand-made fixture ---------------------------------------------------------------------

def test_fixture_schema_is_what_generalize_generates():
    schema, example = build_config_template(
        {name: ConfigField(name, category, False) for name, category in FIELDS.items()})
    assert json.loads((FIXTURE / "generalized" / "config.schema.json").read_text()) == schema
    assert json.loads((FIXTURE / "generalized" / "config.example.json").read_text()) == example


def test_matching_config_restores_the_original_skill(bundle, values, tmp_path):
    result, out = run(bundle, tmp_path, values)
    assert result["status"] == "ok"
    assert tree(out / SKILL_DIR) == tree(FIXTURE / "original")
    assert result["counts"] == {"files": 2, "substitutions": 8, "placeholders_resolved": 4}
    assert result["unresolved"] == [] and result["unused_config"] == [] and result["warnings"] == []
    assert result["artifacts"] == [str(out / SKILL_DIR), str(out / REPORT_FILE)]


def test_report_lists_placeholders_without_values(bundle, values, tmp_path):
    result, out = run(bundle, tmp_path, values)
    report = json.loads((out / REPORT_FILE).read_text(encoding="utf-8"))
    assert report["placeholders_resolved"] == {
        "build_host": 2, "contact_email": 3, "repository_url": 1, "workdir": 2}
    blob = json.dumps(report) + json.dumps(result)
    assert not [v for v in values.values() if v in blob]


def test_inputs_are_not_modified(bundle, values, tmp_path):
    config = write_json(tmp_path / "config.json", values)
    before = tree(bundle), config.read_bytes()
    personalize_skill(bundle, config, tmp_path / "out")
    assert (tree(bundle), config.read_bytes()) == before


def test_repeated_runs_produce_identical_output(bundle, values, tmp_path):
    config = write_json(tmp_path / "config.json", values)
    personalize_skill(bundle, config, tmp_path / "a")
    personalize_skill(bundle, config, tmp_path / "b")
    assert tree(tmp_path / "a") == tree(tmp_path / "b")


def test_the_generalized_skill_folder_itself_is_accepted(bundle, values, tmp_path):
    config = write_json(tmp_path / "config.json", values)
    result = personalize_skill(bundle / "generalized-skill", config, tmp_path / "out")
    assert result["status"] == "ok"


def test_routes_through_the_metaskill_tool(bundle, values, tmp_path):
    sys.path.insert(0, str(Path(__file__).parent.parent))
    import tools

    config = write_json(tmp_path / "config.json", values)
    response = json.loads(tools.metaskill({
        "operation": "personalize", "skill_path": str(bundle),
        "config_path": str(config), "output_path": str(tmp_path / "out")}))
    assert response["success"] is True
    assert response["counts"]["substitutions"] == 8
    assert not [v for v in values.values() if v in json.dumps(response)]


# Bad configuration is refused with clear errors, and nothing is written ---------------------

@pytest.mark.parametrize("change, message", [
    ({"workdir": None}, "'workdir' is required but missing"),
    ({"workdir": ""}, "'workdir' is required but missing"),
    ({"workdir": 42}, "'workdir' must be a string, not a number"),
    ({"workdir": ["a"]}, "'workdir' must be a string, not an array"),
    ({"contact_email": "not-an-address"}, "'contact_email' is not a valid email"),
    ({"repository_url": "two words"}, "'repository_url' is not a valid uri"),
    ({"build_host": "host\nname: injected"}, "'build_host' contains a line break"),
    ({"region": "ca-central-1"}, "'region' is not declared in the schema"),
], ids=["absent", "blank", "number", "array", "email", "uri", "newline", "unexpected"])
def test_invalid_config_is_rejected(bundle, values, tmp_path, change, message):
    config = {k: v for k, v in {**values, **change}.items() if v is not None}
    result, out = run(bundle, tmp_path, config)
    assert result["status"] == "error"
    assert any(message in e for e in result["errors"])
    assert not out.exists()
    assert not list(tmp_path.glob(".out-*"))


def test_every_problem_is_reported_at_once(bundle, tmp_path):
    result, _ = run(bundle, tmp_path, {"contact_email": "nope"})
    assert len(result["errors"]) == 4


@pytest.mark.parametrize("value", [
    "https://github.com/acme-example/payments-service",
    "git@github.com:acme-example/payments-service.git",
    "github.com/acme-example/payments-service",
    "ssh://git@gitlab.com/acme-example/payments-service",
])
def test_repository_forms_the_detector_finds_are_valid_uris(bundle, values, tmp_path, value):
    result, _ = run(bundle, tmp_path, {**values, "repository_url": value})
    assert result["status"] == "ok"


@pytest.mark.parametrize("text, message", [
    ("{", "config: not valid JSON (line 1, column 2)"),
    ('{"workdir": "a", "workdir": "b"}', "config: duplicate key 'workdir'"),
    ("[]", "config: expected a JSON object"),
])
def test_malformed_config_files_are_rejected(bundle, tmp_path, text, message):
    config = tmp_path / "config.json"
    config.write_text(text, encoding="utf-8")
    result = personalize_skill(bundle, config, tmp_path / "out")
    assert result["status"] == "error"
    assert any(e.startswith(message) for e in result["errors"])


def test_errors_never_repeat_a_value(bundle, values, tmp_path):
    secret = fake_secrets.GITHUB_TOKEN
    result, _ = run(bundle, tmp_path, {**values, "contact_email": secret, secret: "x"})
    assert result["status"] == "error"
    assert secret not in json.dumps(result)
    assert "a key that is not a field name is not declared in the schema" in result["error"]


# Schemas are validated before use --------------------------------------------------------

@pytest.mark.parametrize("schema, message", [
    ({"properties": []}, "expected an object with a \"properties\" object"),
    ({"properties": {"Bad-Name": {}}}, "is not a valid placeholder name"),
    ({"properties": {"n": {"type": "integer"}}}, "unsupported type"),
    ({"properties": {"n": {"format": "date"}}}, "unsupported format 'date'"),
    ({"properties": {"n": {"validation": {"pattern": "x"}}}}, "unsupported constraint 'pattern'"),
    ({"properties": {"n": {"format": "email", "validation": {"format": "uri"}}}}, "conflicting"),
    ({"properties": {}, "required": ["n"]}, "required field 'n' is not declared"),
])
def test_unusable_schemas_are_rejected(schema, message):
    with pytest.raises(PersonalizeError) as exc:
        parse_schema(schema)
    assert any(message in e for e in exc.value.errors)


def test_unexpected_fields_are_warnings_when_the_schema_allows_them():
    schema = parse_schema({"properties": {"n": {}}, "additionalProperties": True})
    assert validate_config(schema, {"n": "v", "extra": "w"}) == (
        {"n": "v"}, ["config: 'extra' is not declared in the schema"])


def test_optional_fields_may_be_left_blank():
    schema = parse_schema({"properties": {"n": {"required": False}}})
    assert validate_config(schema, {"n": ""}) == ({}, [])


@pytest.mark.parametrize("files", [
    {}, {"generalized-skill/SKILL.md": "x"}, {"config.schema.json": "{}"}])
def test_input_that_is_not_generalize_output_is_rejected(tmp_path, files, values):
    for rel, text in files.items():
        (tmp_path / "in" / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "in" / rel).write_text(text)
    (tmp_path / "in").mkdir(exist_ok=True)
    result, _ = run(tmp_path / "in", tmp_path, values)
    assert result["status"] == "error"


# Substitution is literal ------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    r"C:\new\1\g<0>", "$HOME && rm -rf /", "{{config.other}}", ".*[a-z]+$", "%s %(x)s {0}"])
def test_values_are_inserted_exactly_and_never_interpreted(value):
    result = substitute("a {{config.key}} b {{config.other}}", {"key": value})
    assert result.text == f"a {value} b {{{{config.other}}}}"
    assert result.unresolved == [("other", 1)]


def test_undeclared_placeholders_are_left_and_reported(tmp_path):
    bundle = make_bundle(tmp_path, {"SKILL.md": "a\n{{config.name}}\n{{config.ghost}}\n"},
                         {"name": Category.CONFIGURATION})
    result, out = run(bundle, tmp_path, {"name": "v"})
    assert result["status"] == "ok"
    assert (out / SKILL_DIR / "SKILL.md").read_text() == "a\nv\n{{config.ghost}}\n"
    assert result["unresolved"] == ["SKILL.md:3 {{config.ghost}}"]
    assert any("not declared in the schema were left unchanged: ghost" in w
               for w in result["warnings"])


def test_unused_config_entries_are_reported(tmp_path):
    bundle = make_bundle(tmp_path, {"SKILL.md": "{{config.used}}"},
                         {"used": Category.CONFIGURATION, "spare": Category.CONFIGURATION})
    result, _ = run(bundle, tmp_path, {"used": "a", "spare": "b"})
    assert result["unused_config"] == ["spare"]
    assert "config entries not used by any placeholder: spare" in result["warnings"]


def test_a_credential_can_be_an_environment_variable_reference(tmp_path):
    bundle = make_bundle(tmp_path, {"run.sh": 'TOKEN="{{config.api_token}}"\n'},
                         {"api_token": Category.CREDENTIAL})
    _, out = run(bundle, tmp_path, {"api_token": "${GITHUB_TOKEN}"})
    assert (out / SKILL_DIR / "run.sh").read_text() == 'TOKEN="${GITHUB_TOKEN}"\n'


def test_credentials_are_restored_but_never_reported(tmp_path):
    token = fake_secrets.GITHUB_TOKEN
    bundle = make_bundle(tmp_path, {"run.sh": 'TOKEN="{{config.api_token}}"\n'},
                         {"api_token": Category.CREDENTIAL})
    result, out = run(bundle, tmp_path, {"api_token": token})
    assert (out / SKILL_DIR / "run.sh").read_text() == f'TOKEN="{token}"\n'
    assert token not in json.dumps(result) + (out / REPORT_FILE).read_text()


# Files and structure are preserved ---------------------------------------------------------

def test_encoding_line_endings_and_other_files_are_preserved(tmp_path):
    files = {
        "SKILL.md": "\ufeffbom {{config.name}}\r\nnext\r\n",
        "assets/logo.png": b"\x89PNG\x00{{config.name}}\x00",
        "notes.txt": "caf\xe9 {{config.name}}".encode("latin-1"),
        "empty.md": "",
    }
    bundle = make_bundle(tmp_path, files, {"name": Category.CONFIGURATION})
    (bundle / "generalized-skill" / "docs" / "empty").mkdir(parents=True)
    result, out = run(bundle, tmp_path, {"name": "v"})
    skill = out / SKILL_DIR
    assert (skill / "SKILL.md").read_bytes() == "\ufeffbom v\r\nnext\r\n".encode("utf-8")
    assert (skill / "assets/logo.png").read_bytes() == files["assets/logo.png"]
    assert (skill / "notes.txt").read_bytes() == files["notes.txt"]
    assert (skill / "empty.md").read_bytes() == b""
    assert (skill / "docs" / "empty").is_dir()
    assert sorted(w for w in result["warnings"] if "copied unchanged" in w) == [
        "assets/logo.png: copied unchanged, binary or too large to substitute",
        "notes.txt: copied unchanged, not UTF-8",
    ]


def test_file_permissions_are_preserved(bundle, values, tmp_path):
    os.chmod(bundle / "generalized-skill" / "scripts" / "deploy.sh", 0o755)
    _, out = run(bundle, tmp_path, values)
    assert (out / SKILL_DIR / "scripts" / "deploy.sh").stat().st_mode & 0o777 == 0o755


def test_symlinks_are_skipped_not_followed(bundle, values, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    os.symlink(outside, bundle / "generalized-skill" / "link.txt")
    os.symlink(tmp_path, bundle / "generalized-skill" / "linkdir")
    result, out = run(bundle, tmp_path, values)
    assert not (out / SKILL_DIR / "link.txt").exists()
    assert not (out / SKILL_DIR / "linkdir").exists()
    assert "link.txt: skipped, not a regular file" in result["warnings"]
    assert "linkdir: symlink skipped, not followed" in result["warnings"]


# Output paths --------------------------------------------------------------------------

def test_an_existing_output_is_never_overwritten(bundle, values, tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "keep.txt").write_text("earlier result")
    result, out = run(bundle, tmp_path, values)
    assert result["errors"] == ["output path: already exists and is not an empty directory"]
    assert tree(out) == {"keep.txt": b"earlier result"}


def test_an_empty_output_directory_is_used(bundle, values, tmp_path):
    (tmp_path / "out").mkdir()
    result, out = run(bundle, tmp_path, values)
    assert result["status"] == "ok"
    assert (out / REPORT_FILE).is_file()


@pytest.mark.parametrize("inside", ["bundle", "bundle/out", "bundle/generalized-skill/out"])
def test_output_inside_the_input_is_rejected(bundle, values, tmp_path, inside):
    config = write_json(tmp_path / "config.json", values)
    before = tree(bundle)
    result = personalize_skill(bundle, config, tmp_path / inside)
    assert result["errors"] == ["output path: must be outside the generalized skill"]
    assert tree(bundle) == before


def test_a_symlinked_output_is_rejected(bundle, values, tmp_path):
    (tmp_path / "real").mkdir()
    os.symlink(tmp_path / "real", tmp_path / "out")
    result, _ = run(bundle, tmp_path, values)
    assert result["errors"] == ["output path: refusing to write through a symlink"]
