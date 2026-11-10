"""Tool schemas — what the LLM sees.

Keep descriptions short: this schema is sent to the model on every turn.
"""

OPERATIONS = ("generalize", "personalize", "evaluate", "rank")

METASKILL = {
    "name": "metaskill",
    "description": (
        "Turn a Hermes skill into a shareable template (generalize), fill a template "
        "with a user's config (personalize), score a skill (evaluate) or compare "
        "skills (rank). Detected values are never returned."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": list(OPERATIONS),
                "description": (
                    "generalize: replace user-specific values in a skill with "
                    "placeholders. personalize: apply a config to a generalized skill. "
                    "evaluate: score one skill. rank: compare several."
                ),
            },
            "skill_path": {
                "type": "string",
                "description": (
                    "Path to the skill directory or SKILL.md. If the user gave a skill "
                    "name, locate its directory first and pass that path. For rank, a "
                    "comma separated list. If the user named no skills for evaluate or "
                    "rank, ask: all installed skills, or a set they list."
                ),
            },
            "config_path": {
                "type": "string",
                "description": (
                    "Path to the JSON config file. Required for personalize; optional "
                    "evaluation settings for evaluate and rank."
                ),
            },
            "output_path": {
                "type": "string",
                "description": (
                    "Directory to write results to. Defaults to ./metaskill-output/, "
                    "or ./evaluation-reports/ for evaluate and rank."
                ),
            },
        },
        "required": ["operation", "skill_path"],
    },
}
