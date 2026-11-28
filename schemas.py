"""Tool schemas — what the LLM sees.

Keep descriptions short: this schema is sent to the model on every turn.
Step-by-step guidance lives in the bundled skill (skills/guide/SKILL.md).
"""

# settings only reads evaluate's saved settings: the user changes them with /metaskill settings.
OPERATIONS = ("generalize", "personalize", "evaluate", "rank", "settings")

METASKILL = {
    "name": "metaskill",
    "description": (
        "Generalize, personalize, evaluate or rank Hermes skills. "
        "Read skill metaskill:guide first. Returns counts and paths, "
        "never detected values; do not quote them to the user."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": list(OPERATIONS),
            },
            "skill_path": {
                "type": "string",
                "description": "Skill directory or SKILL.md path, not a name. Comma list for rank.",
            },
            "config_path": {
                "type": "string",
                "description": "JSON config file. Required for personalize.",
            },
            # Self-explanatory names, and the schema is sent every turn: their guidance,
            # including that tasks_path costs model calls, is in skills/guide/SKILL.md.
            "output_path": {"type": "string"},
            "tasks_path": {"type": "string"},
        },
        "required": ["operation"],
    },
}
