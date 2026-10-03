"""Tool handlers — what runs when the LLM calls each tool."""

import json


def metaskill(args, **kwargs):
    return json.dumps({"success": True, "message": "metaskill is not implemented yet"})
