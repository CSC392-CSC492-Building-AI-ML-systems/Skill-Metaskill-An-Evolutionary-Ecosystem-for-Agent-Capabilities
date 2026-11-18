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
_UNUSED_BODY = "bc4511fcaa44d0581dea3cdb730a38050f47e81458b44c591f025a7ee0c83dfd"


def configuration_evidence(skill: Skill) -> tuple[tuple[FixedSettingEvidence, ...], tuple[str, ...]]:
    """Return fixed settings and known unused declarations for unchanged pilot bundles."""
    path = skill.path.resolve()
    if path.parent != _SKILLS.resolve():
        return (), ()
    digest = sha256(skill.body.encode("utf-8")).hexdigest()
    if path.name == "hardcoded" and digest == _HARDCODED_BODY and not skill.files:
        return (_FIXED_ORGANIZATION,), ()
    files = {Path("config.json"), Path("config.schema.json")}
    if path.name == "unused" and digest == _UNUSED_BODY and set(skill.files) == files:
        return (), ("legacy_region",)
    return (), ()
