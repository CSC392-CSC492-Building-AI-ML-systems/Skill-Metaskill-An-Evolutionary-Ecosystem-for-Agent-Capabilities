"""Metaskill Hermes plugin — registration."""

import functools

from . import schemas, tools
from .metaskill.evaluate.judge import JUDGE_TASK


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
    ctx.register_auxiliary_task(
        JUDGE_TASK,
        display_name="Metaskill judge",
        description="Judges skills for metaskill evaluate (clarity, safety review, task checklists).",
    )
    ctx.register_command(
        "metaskill",
        handler=_handle_metaskill,
        description="Generalize, personalize, evaluate, or merge skills",
    )
