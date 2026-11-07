"""Tool schemas — what the LLM sees.

Keep descriptions short: this schema is sent to the model on every turn.
Step-by-step guidance lives in the bundled skill (skills/guide/SKILL.md).
"""

OPERATIONS = ("generalize", "personalize")

METASKILL = {
    "name": "metaskill",
    "description": (
        "Generalize a Hermes skill into a template, or personalize a template "
        "from a config. Read skill metaskill:guide first. Returns counts and paths, "
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
                "description": "Skill directory or SKILL.md path, not a name.",
            },
            "config_path": {
                "type": "string",
                "description": "JSON config file. Required for personalize.",
            },
            "output_path": {
                "type": "string",
                "description": "Output directory. Optional.",
            },
        },
        "required": ["operation", "skill_path"],
    },
}
