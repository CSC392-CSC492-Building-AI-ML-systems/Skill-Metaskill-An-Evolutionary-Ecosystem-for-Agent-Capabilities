"""Tool schemas — what the LLM sees.

Keep descriptions short: this schema is sent to the model on every turn.
"""

OPERATIONS = ("generalize", "personalize")

METASKILL = {
    "name": "metaskill",
    "description": (
        "Turn a Hermes skill into a shareable template (generalize) or fill a "
        "template with a user's config (personalize). Detected values are never "
        "returned, only counts and file paths."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": list(OPERATIONS),
                "description": (
                    "generalize: replace user-specific values in a skill with "
                    "placeholders. personalize: apply a config to a generalized skill."
                ),
            },
            "skill_path": {
                "type": "string",
                "description": (
                    "Path to the skill directory or SKILL.md. If the user gave a skill "
                    "name, locate its directory first and pass that path."
                ),
            },
            "config_path": {
                "type": "string",
                "description": "Path to the JSON config file. Required for personalize.",
            },
            "output_path": {
                "type": "string",
                "description": "Directory to write results to. Defaults to ./metaskill-output/.",
            },
        },
        "required": ["operation", "skill_path"],
    },
}
