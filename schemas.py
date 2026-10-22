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
                    "generalize: detect user-specific personal data in a skill and "
                    "report counts (nothing is rewritten yet). personalize: apply a "
                    "config to a generalized skill."
                ),
            },
            "skill_path": {
                "type": "string",
                "description": (
                    "Path to the skill directory or SKILL.md. If the user gave a skill "
                    "name, locate its directory first and pass that path."
                ),
            },
            "use_laya": {
                "type": "boolean",
                "description": (
                    "generalize only. Opt in to the local Laya Experts PII model for "
                    "detection (~1GB, downloaded once, no API key). Off by default."
                ),
            },
            "laya_model_path": {
                "type": "string",
                "description": (
                    "generalize only, with use_laya. Path to a local Laya PII model "
                    "directory; leave empty to use the public Hugging Face model."
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
