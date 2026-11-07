"""Tool handlers — what runs when the LLM calls each tool.

The handler validates arguments and routes to the generalize, personalize,
evaluate and rank pipelines. Detected values, quotes and finding text must never
be put in a response, because the response goes into the agent's context.

Handlers must return a JSON string on every path and never raise.

Plugins run in the Hermes gateway, whose own folder is the Hermes install, so a relative
path means the folder of the user's session, not os.getcwd().
"""

import importlib
import json
import os
import re

DEFAULT_OUTPUT_DIR = "metaskill-output"

USAGE = (
    "usage: /metaskill generalize <skill> | "
    "/metaskill personalize <skill> --config <file> | "
    "/metaskill evaluate <skill> | /metaskill rank <skills>"
)

# operation -> (module, entry point)
_ENTRY_POINTS = {
    "generalize": ("metaskill.generalize", "generalize_skill"),
    "personalize": ("metaskill.personalize", "personalize_skill"),
    "evaluate": ("metaskill.evaluate", "evaluate_skill"),
    "rank": ("metaskill.evaluate", "rank_skills"),
}
EVALUATION = ("evaluate", "rank")  # these write to evaluation-reports/ and need ctx


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


def _session_cwd(ctx):
    """The folder of the user's session, or "" on a Hermes without ctx.session_cwd."""
    session_cwd = getattr(ctx, "session_cwd", None)
    return session_cwd() if callable(session_cwd) else ""


def _skill_paths(operation, skill_path, cwd=""):
    """The skills of a request, under cwd. rank takes a comma separated list
    (docs/evaluate.md 2); a path that exists is one skill, even with a comma in it."""
    whole = os.path.join(cwd, skill_path)
    if operation != "rank" or os.path.exists(whole):
        return [whole]
    paths = (path.strip().strip("\"'") for path in skill_path.split(","))
    return [os.path.join(cwd, path) for path in paths if path]


def run(operation, skill_path, config_path=None, output_path=None, ctx=None):
    """Validate arguments, route to the pipeline, and return a response dict."""
    if operation not in _ENTRY_POINTS:
        return _error(operation, f"unknown operation; expected one of {', '.join(_ENTRY_POINTS)}")
    cwd = _session_cwd(ctx)
    config_path = config_path and os.path.join(cwd, config_path)
    output_path = output_path and os.path.join(cwd, output_path)
    paths = _skill_paths(operation, skill_path, cwd) if skill_path else []
    if not paths:
        if operation in EVALUATION:  # docs/evaluate.md section 7
            return _error(operation, "skill_path is required; ask the user which skills "
                                     "to use: all installed skills, or a set they list")
        return _error(operation, "skill_path is required")
    if operation == "personalize" and not config_path:
        return _error(operation, "config_path is required for personalize")

    # The model resolves skill names to paths; point it back to that step on a miss.
    unknown = [path for path in paths if not os.path.exists(path)]
    if unknown:
        return _error(
            operation,
            f"skill_path does not exist: {unknown[0]}; find the skill's directory from its "
            "name and retry with that path",
        )
    if operation != "generalize" and config_path and not os.path.exists(config_path):
        return _error(operation, "config_path does not exist")

    entry_point = _load_entry_point(operation)
    if entry_point is None:
        return _error(operation, f"{operation} is not implemented yet")

    target = paths if operation == "rank" else paths[0]
    if operation in EVALUATION:
        # output_path stays None by default: the config says where reports go.
        kwargs = {"config_path": config_path, "output_path": output_path, "ctx": ctx}
    else:
        default = os.path.join(cwd, default_output_path(operation, skill_path))
        kwargs = {"output_path": output_path or default}
        if operation == "personalize":
            kwargs["config_path"] = config_path
    try:
        result = entry_point(target, **kwargs)
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


def metaskill(args, ctx=None, **kwargs):
    try:
        args = args if isinstance(args, dict) else {}
        result = run(
            args.get("operation"),
            args.get("skill_path"),
            config_path=args.get("config_path"),
            output_path=args.get("output_path"),
            ctx=ctx,
        )
        result["success"] = result["status"] == "ok"
        return json.dumps(result)
    except Exception as exc:  # last resort: handlers must not raise
        return json.dumps(
            {"success": False, "status": "error",
                "error": f"metaskill failed: {type(exc).__name__}"}
        )


_COMMAND = re.compile(r"(\S+)\s+(.+?)(?:\s+--config\s+(.+))?")


def _parse_command(request):
    """(operation, skills, config file) of `<operation> <skills> [--config FILE]`, or
    None if the request has another shape."""
    match = _COMMAND.fullmatch(request)
    if match is None or match[1] not in _ENTRY_POINTS:
        return None
    operation, skills, config_path = match.groups()
    return operation, skills.strip("\"'"), config_path and config_path.strip("\"'")


def _summary(result):
    """A response as text for the user. Like the response, it holds no detected values."""
    if result["status"] == "error":
        lines = [f"metaskill {result['operation']} failed: {result['error']}"]
    else:
        lines = [result.get("table") or f"metaskill {result['operation']} finished."]
    lines += [f"Warning: {warning}" for warning in result.get("warnings", [])]
    for missing in result.get("dependencies", []):
        how = missing["install_command"] or missing["install_url"]
        lines.append(f"Not installed: {missing['package']}" + (f" ({how})" if how else ""))
    if result.get("unresolved"):
        lines.append(f"Unresolved: {len(result['unresolved'])}")
    lines += [f"Wrote: {path}" for path in result.get("artifacts", [])]
    return "\n".join(lines)


def make_command_handler(ctx):
    """Build the /metaskill handler.

    A request naming skills by path runs here and prints its result. Anything else, such
    as a skill name, goes to the agent as a user message, so the model can find the skill
    and call the metaskill tool. Hermes only allows that for a plugin with
    allow_gateway_injection, and a classic CLI session does not show the agent's reply.
    """

    def handle_command(raw_args):
        request = (raw_args or "").strip()
        if not request:
            return USAGE
        try:
            parsed = _parse_command(request)
            if parsed:
                operation, skills, config_path = parsed
                paths = _skill_paths(operation, skills, _session_cwd(ctx))
                if all(os.path.exists(path) for path in paths):
                    return _summary(run(operation, skills, config_path=config_path, ctx=ctx))
            if not ctx.inject_message(f"Use the metaskill tool to: {request}"):
                return ("metaskill: could not send the request to the agent. Give the skill's "
                        "path, or set plugins.entries.metaskill.allow_gateway_injection: true "
                        "in config.yaml to let the agent find it by name.")
            return None
        except Exception as exc:  # report the type only: messages may echo file contents
            return f"metaskill failed: {type(exc).__name__}"

    return handle_command
