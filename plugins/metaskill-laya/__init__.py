"""metaskill-laya: an optional add-on that adds Laya Experts PII detection to metaskill.

metaskill's generalize works without it. When this plugin is enabled, generalize also
runs the Laya model over each file, after the regex layer and skipping what that layer
already found. Disable or uninstall the plugin to turn it off.

Registering is cheap: laya, torch and the model load on the first scan, not here.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

from . import cli, detector

# Must match metaskill/generalize/extras.py. The two plugins cannot import each other,
# so they share a registry kept in sys.modules under this name.
REGISTRY = "metaskill_extras"
ENTRY = {"api": 1, "name": "laya", "detect": detector.detect}

# Setup and GPU instructions, offered to the agent as metaskill-laya:setup and loaded on
# demand. The same file is the setup documentation in the repo.
SETUP_SKILL = Path(__file__).parent / "skills" / "setup" / "SKILL.md"
SETUP_DESCRIPTION = (
    "Set up or troubleshoot the metaskill-laya add-on: model download, GPU acceleration, "
    "and laya warnings in generalize results."
)
# Hermes reads index conditions from the registration, not the file; this repeats the
# SKILL.md frontmatter.
SETUP_FRONTMATTER = {"metadata": {"hermes": {"requires_tools": ["metaskill"]}}}


def _registry() -> dict:
    fresh = types.ModuleType(REGISTRY)
    fresh.detectors = {}
    return sys.modules.setdefault(REGISTRY, fresh).detectors


def _unregister() -> None:
    registry = _registry()
    if registry.get(ENTRY["name"]) is ENTRY:  # a reload may already have replaced it
        del registry[ENTRY["name"]]


def register(ctx):
    _registry()[ENTRY["name"]] = ENTRY
    # Older Hermes versions lack these; the detector still works without them.
    if hasattr(ctx, "on_unload"):
        ctx.on_unload(_unregister)
    if hasattr(ctx, "register_cli_command"):
        ctx.register_cli_command(
            "metaskill-laya",
            help="Check the Laya add-on or download its model",
            setup_fn=cli.setup,
            handler_fn=cli.handle,
        )
    if hasattr(ctx, "register_skill"):
        try:
            ctx.register_skill("setup", SETUP_SKILL, description=SETUP_DESCRIPTION,
                               frontmatter=SETUP_FRONTMATTER)
        except TypeError:  # versions documented as register_skill(name, path) only
            ctx.register_skill("setup", SETUP_SKILL)
