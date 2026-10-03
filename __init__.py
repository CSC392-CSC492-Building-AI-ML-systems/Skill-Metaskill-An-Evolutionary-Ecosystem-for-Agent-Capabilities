"""Metaskill Hermes plugin — registration."""

from . import schemas, tools


def _handle_metaskill(raw_args):
    return "metaskill is not implemented yet"


def register(ctx):
    ctx.register_tool(
        name="metaskill",
        toolset="metaskill",
        schema=schemas.METASKILL,
        handler=tools.metaskill,
    )
    ctx.register_command(
        "metaskill",
        handler=_handle_metaskill,
        description="Generalize, personalize, evaluate, or merge skills",
    )
