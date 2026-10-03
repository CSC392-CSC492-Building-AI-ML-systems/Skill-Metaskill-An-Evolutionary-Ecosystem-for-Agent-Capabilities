"""Tool schemas — what the LLM sees."""

METASKILL = {
    "name": "metaskill",
    "description": "Generalize, personalize, evaluate, or merge Hermes skills.",
    "parameters": {
        "type": "object",
        "properties": {
            "request": {
                "type": "string",
                "description": "What to do, e.g. 'generalize gcp-file-transfer'",
            },
        },
        "required": ["request"],
    },
}
