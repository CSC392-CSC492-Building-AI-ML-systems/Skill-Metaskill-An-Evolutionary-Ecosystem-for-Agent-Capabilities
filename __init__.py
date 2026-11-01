"""Metaskill Hermes plugin — registration."""

import functools
from pathlib import Path

from . import schemas, tools
from .metaskill.evaluate.judge import JUDGE_TASK

GUIDE_SKILL = Path(__file__).parent / "skills" / "guide" / "SKILL.md"
GUIDE_DESCRIPTION = (
    "How to run the metaskill tool: find the skill's path, pick generalize or personalize, "
    "find the config."
)
# Hermes reads index conditions from what is registered, not from the file, so this repeats
# the SKILL.md frontmatter: hide the guide when the metaskill tool is unavailable.
GUIDE_FRONTMATTER = {"metadata": {"hermes": {"requires_tools": ["metaskill"]}}}


def _handle_metaskill(raw_args):
    return "metaskill is not implemented yet"


def register(ctx):
    ctx.register_tool(
        name="metaskill",
        toolset="metaskill",
        schema=schemas.METASKILL,
        # ctx gives evaluate access to ctx.llm, the user's configured model.
        handler=functools.partial(tools.metaskill, ctx=ctx),
    )
    # Older Hermes has no auxiliary tasks. Without this guard the AttributeError would make
    # Hermes undo the whole plugin; the judge then uses the active model (Judge.from_context).
    if hasattr(ctx, "register_auxiliary_task"):
        ctx.register_auxiliary_task(
            JUDGE_TASK,
            display_name="Metaskill judge",
            description="Judges skills for metaskill evaluate (clarity, safety review, task checklists).",
        )
    # How-to guidance for the tool, loaded on demand as metaskill:guide so it stays out of
    # the schema. Same guard as above: older Hermes has no plugin skills.
    if hasattr(ctx, "register_skill"):
        try:
            ctx.register_skill(
                "guide", GUIDE_SKILL, description=GUIDE_DESCRIPTION, frontmatter=GUIDE_FRONTMATTER
            )
        except TypeError:  # versions documented as register_skill(name, path) only
            ctx.register_skill("guide", GUIDE_SKILL)
    ctx.register_command(
        "metaskill",
        handler=tools.make_command_handler(ctx),
        description="Generalize or personalize a skill",
        args_hint="generalize|personalize <skill> [--config FILE]",
    )
