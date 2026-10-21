"""Metaskill Hermes plugin — registration."""

import functools

import functools

from . import schemas, tools
from .metaskill.evaluate.judge import JUDGE_TASK


def _handle_metaskill(raw_args):
    return "metaskill is not implemented yet"


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
    ctx.register_command(
        "metaskill",
        handler=tools.make_command_handler(ctx),
        description="Generalize or personalize a skill",
        args_hint="generalize|personalize <skill> [--config FILE]",
    )
