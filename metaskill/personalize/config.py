"""Loading and validating a generalized skill's schema and a user's configuration (design.md, section 4).

Both files are untrusted data and are only parsed as JSON. Error messages name fields but
never repeat values, because a configuration can hold credentials.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

MAX_JSON_BYTES = 1024 * 1024

_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}")  # the key syntax of {{config.<key>}}
_CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")  # anything but tab could break a file's structure
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_SCP_URI = re.compile(r"[\w.-]+@[\w.-]+:\S+")  # git@github.com:owner/repo
_BARE_URI = re.compile(r"[\w-]+(?:\.[\w-]+)+/\S+")  # github.com/owner/repo


def _is_uri(value: str) -> bool:
    if any(c.isspace() for c in value):
        return False
    parts = urlsplit(value)
    if parts.scheme and parts.netloc:
        return True
    return bool(_SCP_URI.fullmatch(value) or _BARE_URI.fullmatch(value))


FORMATS = {"email": _EMAIL.fullmatch, "uri": _is_uri}
_CONSTRAINTS = frozenset({"format"})


class PersonalizeError(ValueError):
    """Personalization was refused; errors lists every problem found, without values."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class Field:
    name: str
    required: bool = True
    format: str | None = None
    sensitive: bool = False


@dataclass(frozen=True)
class Schema:
    fields: dict[str, Field] = field(default_factory=dict)
    allow_unexpected: bool = False


def _label(key: object) -> str:
    """A key is echoed only when it is a valid field name, so a pasted value never is."""
    return repr(key) if isinstance(key, str) and _KEY.fullmatch(key) else "a key that is not a field name"


def _type_name(value: object) -> str:
    return {bool: "a boolean", int: "a number", float: "a number", list: "an array",
            dict: "an object"}.get(type(value), "null")


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise PersonalizeError([f"duplicate key {_label(key)}"])
        result[key] = value
    return result


def load_json(path: Path | str, label: str) -> object:
    path = Path(path)
    try:
        if path.is_symlink():
            raise PersonalizeError([f"{label}: refusing to read a symlink"])
        if path.stat().st_size > MAX_JSON_BYTES:
            raise PersonalizeError([f"{label}: over the limit of {MAX_JSON_BYTES} bytes"])
        data = path.read_bytes()
    except OSError:
        raise PersonalizeError([f"{label}: cannot be read"]) from None
    try:
        return json.loads(data.decode("utf-8-sig"), object_pairs_hook=_reject_duplicates)
    except UnicodeDecodeError:
        raise PersonalizeError([f"{label}: not UTF-8"]) from None
    except json.JSONDecodeError as exc:  # the position only; exc.doc holds the file
        raise PersonalizeError([f"{label}: not valid JSON (line {exc.lineno}, column {exc.colno})"]) from None
    except PersonalizeError as exc:
        raise PersonalizeError([f"{label}: {e}" for e in exc.errors]) from None


def parse_schema(data: object, label: str = "config.schema.json") -> Schema:
    if not isinstance(data, dict) or not isinstance(data.get("properties"), dict):
        raise PersonalizeError([f"{label}: expected an object with a \"properties\" object"])
    errors: list[str] = []
    required = data.get("required", [])
    if not isinstance(required, list) or not all(isinstance(r, str) for r in required):
        errors.append(f"{label}: \"required\" must be a list of field names")
        required = []
    allow_unexpected = data.get("additionalProperties", True)
    if not isinstance(allow_unexpected, bool):
        errors.append(f"{label}: \"additionalProperties\" must be true or false")

    fields: dict[str, Field] = {}
    for name, spec in data["properties"].items():
        where = f"{label}: field {_label(name)}"
        if not isinstance(name, str) or not _KEY.fullmatch(name):
            errors.append(f"{where} is not a valid placeholder name")
            continue
        if not isinstance(spec, dict):
            errors.append(f"{where} must be an object")
            continue
        if spec.get("type", "string") != "string":
            errors.append(f"{where} has an unsupported type; only strings are supported")
        validation = spec.get("validation", {})
        if not isinstance(validation, dict):
            errors.append(f"{where} has a \"validation\" that is not an object")
            validation = {}
        for constraint in sorted(set(validation) - _CONSTRAINTS):
            errors.append(f"{where} has an unsupported constraint {_label(constraint)}")
        formats = {f for f in (spec.get("format"), validation.get("format")) if f is not None}
        if len(formats) > 1:
            errors.append(f"{where} declares conflicting formats")
        fmt = next(iter(formats), None)
        if fmt is not None and fmt not in FORMATS:
            errors.append(f"{where} has an unsupported format {_label(fmt)}")
        fields[name] = Field(
            name=name,
            required=name in required or spec.get("required") is True,
            format=fmt,
            sensitive=spec.get("sensitive") is True or spec.get("writeOnly") is True,
        )
    for name in required:
        if name not in data["properties"]:
            errors.append(f"{label}: required field {_label(name)} is not declared")
    if errors:
        raise PersonalizeError(errors)
    return Schema(fields, allow_unexpected is True)


def load_schema(path: Path | str) -> Schema:
    return parse_schema(load_json(path, "config.schema.json"))


def validate_config(schema: Schema, config: object) -> tuple[dict[str, str], list[str]]:
    """Return the values to substitute and any warnings, or raise with every problem found.

    A blank string counts as missing, since the generated example config is all blanks.
    """
    if not isinstance(config, dict):
        raise PersonalizeError(["config: expected a JSON object mapping field names to values"])
    errors: list[str] = []
    warnings: list[str] = []
    values: dict[str, str] = {}
    for name, spec in sorted(schema.fields.items()):
        value = config.get(name)
        if value is None or value == "":
            if spec.required:
                errors.append(f"config: {name!r} is required but missing")
            continue
        if not isinstance(value, str):
            errors.append(f"config: {name!r} must be a string, not {_type_name(value)}")
        elif _CONTROL.search(value):
            errors.append(f"config: {name!r} contains a line break or control character")
        elif spec.format and not FORMATS[spec.format](value):
            errors.append(f"config: {name!r} is not a valid {spec.format}")
        else:
            values[name] = value
    for key in config:
        if key not in schema.fields:
            message = f"config: {_label(key)} is not declared in the schema"
            (warnings if schema.allow_unexpected else errors).append(message)
    if errors:
        raise PersonalizeError(errors)
    return values, warnings
