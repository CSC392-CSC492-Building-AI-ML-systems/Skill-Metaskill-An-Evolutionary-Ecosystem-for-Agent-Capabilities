"""Metaskill Hermes plugin — registration."""

from . import schemas, tools


def register(ctx):
    ctx.register_tool(
        name="metaskill",
        toolset="metaskill",
        schema=schemas.METASKILL,
        handler=tools.metaskill,
    )
    ctx.register_command(
        "metaskill",
        handler=tools.make_command_handler(ctx),
        description="Generalize or personalize a skill",
        args_hint="generalize|personalize <skill> [--config FILE]",
    )
