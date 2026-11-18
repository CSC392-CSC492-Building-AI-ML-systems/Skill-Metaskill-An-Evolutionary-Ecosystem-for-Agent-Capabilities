"""Evidence scoped to the bundled, instruction-only ticket-summary fixtures."""

from hashlib import sha256
from pathlib import Path

from ..config_checks import FixedSettingEvidence
from ..models import Skill

_SKILLS = Path(__file__).parent / "synthetic/ticket-summary/skills"
_FIXED_ORGANIZATION = FixedSettingEvidence(
    setting="organization",
    line='Always use organization "Example Alpha". No external configuration is read.',
)
_HARDCODED_BODY = "ea91aacbf914c7244da7b4184aa82684b03710f88592265b0ad41e4667b09ea0"
_CONFIGURED_BODY = "bc4511fcaa44d0581dea3cdb730a38050f47e81458b44c591f025a7ee0c83dfd"


def configuration_evidence(skill: Skill) -> tuple[tuple[FixedSettingEvidence, ...], tuple[str, ...]]:
    """Return fixed settings and known unused declarations for unchanged pilot bundles."""
    path = skill.path.resolve()
    if path.parent != _SKILLS.resolve():
        return (), ()
    digest = sha256(skill.body.encode("utf-8")).hexdigest()
    if path.name == "hardcoded" and digest == _HARDCODED_BODY and not skill.files:
        return (_FIXED_ORGANIZATION,), ()
    files = {Path("config.json"), Path("config.schema.json")}
    if path.name == "unused" and digest == _CONFIGURED_BODY and set(skill.files) == files:
        return (), ("legacy_region",)
    return (), ()


# These reviewed inputs describe a single portable, consumed setting. Pin them
# so an edited bundle cannot inherit the reference fixture's coverage.
_REFERENCE_FILES = {
    Path("config.schema.json"): "849688ae29f8f9e4baf07d0272c67ea9e27c2eb9522e2879c373be0557a8b18d",
    Path("config.json"): "dec748bf7fbbc3a56597ea1153f039a2c5d496f70cc01517eca8216e9e3b26ca",
}


def reference_usage(skill: Skill) -> frozenset[str] | None:
    """Return the reviewed complete usage inventory for the pinned reference."""
    if skill.path.resolve() != (_SKILLS / "reference").resolve():
        return None
    if sha256(skill.body.encode("utf-8")).hexdigest() != _CONFIGURED_BODY:
        return None
    if set(skill.files) != _REFERENCE_FILES.keys():
        return None
    for relative, expected in _REFERENCE_FILES.items():
        path = skill.path / relative
        try:
            if path.is_symlink() or not path.is_file():
                return None
            with path.open("rb") as stream:
                raw = stream.read(4097)
        except OSError:
            return None
        if len(raw) > 4096 or sha256(raw.replace(b"\r\n", b"\n")).hexdigest() != expected:
            return None
    return frozenset({"organization"})
