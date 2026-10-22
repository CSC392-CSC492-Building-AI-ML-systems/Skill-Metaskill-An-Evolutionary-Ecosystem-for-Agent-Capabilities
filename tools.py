"""Tool handlers — what runs when the LLM calls each tool.

The handler validates arguments and routes to the generalize/personalize
pipelines. The pipelines return counts and paths only; detected values must
never be put in a response, because the response goes into the agent's context.

Handlers must return a JSON string on every path and never raise.
"""

import importlib
import json
import os

DEFAULT_OUTPUT_DIR = "metaskill-output"

USAGE = (
    "usage: /metaskill generalize <skill> | "
    "/metaskill personalize <skill> --config <file>"
)

# operation -> (module, entry point)
_ENTRY_POINTS = {
    "generalize": ("metaskill.generalize", "generalize_skill"),
    "personalize": ("metaskill.personalize", "personalize_skill"),
}


def _error(operation, message):
    return {"operation": operation, "status": "error", "error": message}


def _skill_name(skill_path):
    path = os.path.normpath(skill_path)
    if os.path.basename(path).lower() == "skill.md":
        path = os.path.dirname(path)
    return os.path.basename(path) or "skill"


def default_output_path(operation, skill_path):
    suffix = "template" if operation == "generalize" else "personalized"
    return os.path.join(DEFAULT_OUTPUT_DIR, f"{_skill_name(skill_path)}-{suffix}")


def _load_entry_point(operation):
    """Import the pipeline lazily so the plugin loads before it is implemented."""
    module_name, func_name = _ENTRY_POINTS[operation]
    if __package__:
        module_name = f"{__package__}.{module_name}"
    try:
        return getattr(importlib.import_module(module_name), func_name)
    except (ImportError, AttributeError):
        return None


def run(operation, skill_path, config_path=None, output_path=None,
        use_laya=None, laya_model_path=None):
    """Validate arguments, route to the pipeline, and return a response dict."""
    if operation not in _ENTRY_POINTS:
        return _error(operation, f"unknown operation; expected one of {', '.join(_ENTRY_POINTS)}")
    if not skill_path:
        return _error(operation, "skill_path is required")
    if operation == "personalize" and not config_path:
        return _error(operation, "config_path is required for personalize")

    # The model resolves skill names to paths; point it back to that step on a miss.
    if not os.path.exists(skill_path):
        return _error(
            operation,
            "skill_path does not exist; find the skill's directory from its name and retry with that path",
        )
    if operation == "personalize" and not os.path.exists(config_path):
        return _error(operation, "config_path does not exist")

    output_path = output_path or default_output_path(operation, skill_path)

    entry_point = _load_entry_point(operation)
    if entry_point is None:
        return _error(operation, f"{operation} is not implemented yet")

    kwargs = {"output_path": output_path}
    if operation == "personalize":
        kwargs["config_path"] = config_path
    if operation == "generalize" and use_laya:
        kwargs["use_laya"] = True
        if laya_model_path:
            kwargs["laya_model_path"] = laya_model_path
    try:
        result = entry_point(skill_path, **kwargs)
    except Exception as exc:  # report the type only: messages may echo file contents
        return _error(operation, f"{operation} failed: {type(exc).__name__}")

    response = {
        "operation": operation,
        "status": "ok",
        "artifacts": [],
        "counts": {},
        "unresolved": [],
        "warnings": [],
    }
    response.update(result or {})
    response["operation"] = operation
    return response


def metaskill(args, **kwargs):
    try:
        args = args if isinstance(args, dict) else {}
        result = run(
            args.get("operation"),
            args.get("skill_path"),
            config_path=args.get("config_path"),
            output_path=args.get("output_path"),
            use_laya=args.get("use_laya"),
            laya_model_path=args.get("laya_model_path"),
        )
        result["success"] = result["status"] == "ok"
        return json.dumps(result)
    except Exception as exc:  # last resort: handlers must not raise
        return json.dumps(
            {"success": False, "status": "error", "error": f"metaskill failed: {type(exc).__name__}"}
        )


def make_command_handler(ctx):
    """Build the /metaskill handler.

    The command forwards the request to the agent as a user message, so the
    model can resolve a skill name to a path and call the metaskill tool.
    """

    def handle_command(raw_args):
        request = (raw_args or "").strip()
        if not request:
            return USAGE
        if not ctx.inject_message(f"Use the metaskill tool to: {request}"):
            return "metaskill: could not send the request to the agent."
        return None

    return handle_command
