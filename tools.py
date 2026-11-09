"""Tool handlers — what runs when the LLM calls each tool.

The handler validates arguments and routes to the generalize, personalize,
evaluate and rank pipelines. Detected values, quotes and finding text must never
be put in a response, because the response goes into the agent's context.

Handlers must return a JSON string on every path and never raise.

Plugins run in the Hermes gateway, whose own folder is the Hermes install, so a relative
path means the folder of the user's session, not os.getcwd().
"""

import difflib
import importlib
import json
import os
import re
import unicodedata

DEFAULT_OUTPUT_DIR = "metaskill-output"

SKILL_HELP = (
    "A skill is a name (claude-code), a category/name (autonomous-ai-agents/claude-code) "
    "or a path. /metaskill skills [text] lists the ones you can use."
)
USAGE = (
    "usage: /metaskill evaluate <skill> | rank <skill>, <skill> | generalize <skill> | "
    f"personalize <skill> --config <file> | skills [text]\n{SKILL_HELP}"
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


def _skill_names(operation, skill_path, cwd=""):
    """The skills of a request as given. rank takes a comma separated list
    (docs/evaluate.md 2); a path that exists is one skill, even with a comma in it."""
    if operation != "rank" or os.path.exists(os.path.join(cwd, skill_path)):
        return [skill_path]
    names = (name.strip().strip("\"'") for name in skill_path.split(","))
    return [name for name in names if name]


def _installed_skills():
    """(folder, category/name, the lowercase names it goes by) of each skill Hermes has
    installed. It goes by its folder name, category/name and frontmatter name. Empty
    outside Hermes."""
    try:
        from agent.skill_utils import (
            get_all_skills_dirs,
            iter_skill_index_files,
            parse_frontmatter,
        )
    except ImportError:
        return []
    skills = []
    for root in get_all_skills_dirs():
        for skill_md in iter_skill_index_files(root, "SKILL.md"):
            folder = skill_md.parent
            relative = folder.relative_to(root).as_posix()
            names = {folder.name, relative}
            try:
                frontmatter, _ = parse_frontmatter(skill_md.read_text(encoding="utf-8-sig"))
                name = frontmatter.get("name")
                if isinstance(name, str) and name:  # str() of a crafted value takes minutes
                    names.add(name)
            except (OSError, UnicodeDecodeError):
                pass  # still found by its folder
            skills.append((str(folder), relative, {name.casefold() for name in names}))
    return skills


# Folders not searched for skills, besides hidden ones such as .git and .venv.
_NOT_SEARCHED = {"node_modules", "__pycache__", "venv", "site-packages"}
_SEARCH_DEPTH = 5  # folders below the session folder
_SEARCH_LIMIT = 5000  # folders looked at, so a session in a large folder stays quick
_LISTED_GROUPS = 12  # lines of skills per section in /metaskill skills


def _local_skills(cwd):
    """The skill folders under cwd, relative and with /, and whether the search stopped
    at _SEARCH_LIMIT folders."""
    found, seen = [], 0
    for folder, subfolders, files in os.walk(cwd):
        seen += 1
        relative = os.path.relpath(folder, cwd)
        if "SKILL.md" in files:
            found.append(relative.replace(os.sep, "/"))
            subfolders[:] = []  # a skill's own folders are not skills
        elif relative != "." and relative.count(os.sep) + 1 >= _SEARCH_DEPTH:
            subfolders[:] = []
        else:
            subfolders[:] = sorted(name for name in subfolders
                                   if not name.startswith(".") and name not in _NOT_SEARCHED)
        if seen >= _SEARCH_LIMIT:
            return sorted(found), True
    return sorted(found), False


def _suggestions(name, installed, local):
    """Up to three skills that a name which was not found may have meant."""
    wanted = name.replace("\\", "/").strip("/").casefold()
    options = sorted({relative for _, relative, _ in installed} | set(local))
    picks = [option for option in options if wanted and wanted in option.casefold()]
    by_name = {}
    for option in options:
        by_name.setdefault(option.rpartition("/")[2].casefold(), []).append(option)
    for close in difflib.get_close_matches(wanted.rpartition("/")[2], list(by_name), n=3):
        picks += by_name[close]
    return list(dict.fromkeys(picks))[:3]


def _find_skills(operation, skills, cwd):
    """The folders of the skills in a command, and what could not be found, with
    suggestions. A path that exists wins over a name."""
    paths, problems, installed, local = [], [], None, None
    for name in _skill_names(operation, skills, cwd):
        path = os.path.join(cwd, name)
        if os.path.exists(path):
            paths.append(path)
            continue
        installed = _installed_skills() if installed is None else installed
        wanted = name.replace("\\", "/").strip("/").casefold()
        found = [(folder, relative) for folder, relative, names in installed if wanted in names]
        if len(found) == 1:
            paths.append(found[0][0])
        elif found:
            shown = [relative for _, relative in found]
            if len(set(shown)) < len(shown):  # the same category/name in two skill folders
                shown = [folder for folder, _ in found]
            problems.append(f"{name} matches {len(found)} skills: {', '.join(shown)}")
        else:
            local = _local_skills(cwd or os.getcwd())[0] if local is None else local
            close = _suggestions(name, installed, local)
            problems.append(f"No skill found for {name}."
                            + (f" Did you mean {', '.join(close)}?" if close else ""))
    return paths, problems


def _where(cwd):
    """Which folder relative paths start from: the session's, not Hermes' own."""
    return f"Folder: {cwd} (relative paths start here)"


def _grouped(paths):
    """`  category: name, name` lines for skill paths, at most _LISTED_GROUPS of them."""
    groups = {}
    for path in paths:
        parent, _, name = path.rpartition("/")
        groups.setdefault(parent or ".", []).append("(this folder)" if path == "." else name)
    lines = [f"  {parent}: {', '.join(names)}" for parent, names in groups.items()]
    if len(lines) > _LISTED_GROUPS:
        lines = lines[:_LISTED_GROUPS] + [f"  and {len(lines) - _LISTED_GROUPS} more folders"]
    return lines


def _list_skills(text, cwd):
    """`/metaskill skills [text]`: where paths start, and the skills under that folder and
    installed in Hermes whose path or name contains text."""
    wanted = text.strip().casefold()
    local, stopped = _local_skills(cwd)
    local = [path for path in local if wanted in path.casefold()]
    installed = sorted(relative for _, relative, names in _installed_skills()
                       if wanted in relative.casefold() or any(wanted in n for n in names))
    lines = [_where(cwd)]
    if local:
        lines += [f"Skills under this folder ({len(local)}):", *_grouped(local)]
    if stopped:
        lines.append(f"  (only the first {_SEARCH_LIMIT} folders were searched)")
    if installed:
        lines += [f"Installed skills ({len(installed)}):", *_grouped(installed)]
    if not local and not installed:
        lines.append(f"No skills match {text.strip()}." if wanted else "No skills found.")
    lines.append("Run one with /metaskill evaluate <name, category/name or path>.")
    return "\n".join(lines)


def run(operation, skill_path, config_path=None, output_path=None, ctx=None):
    """Validate arguments, route to the pipeline, and return a response dict."""
    if operation not in _ENTRY_POINTS:
        return _error(operation, f"unknown operation; expected one of {', '.join(_ENTRY_POINTS)}")
    cwd = _session_cwd(ctx)
    config_path = config_path and os.path.join(cwd, config_path)
    output_path = output_path and os.path.join(cwd, output_path)
    names = skill_path if isinstance(skill_path, list) else (
        _skill_names(operation, skill_path, cwd) if skill_path else [])
    paths = [os.path.join(cwd, name) for name in names]
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
        # From the resolved path: skill_path can be a list, which has no name to take.
        default = os.path.join(cwd, default_output_path(operation, target))
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


_CELL = re.compile(r"(?<!\\)\|")  # a column border, not an escaped | in a name
_ESCAPED = re.compile(r"\\([\\`\[\]<>|])")  # what rank.py escapes for Markdown


def _printable(text):
    """text without control characters other than line breaks, so that nothing read from
    disk or from a skill can send the terminal an escape sequence."""
    return "".join(c if c == "\n" or unicodedata.category(c) != "Cc" else " " for c in text)


def _plain(text):
    """A Markdown table and the lines after it as plain text: columns lined up and the
    Markdown escapes removed, since Hermes prints a command's output as it is."""
    text = _printable(text)
    out, rows = [], []

    def flush():
        if not rows:
            return
        width = max(len(row) for row in rows)
        rows[:] = [row + [""] * (width - len(row)) for row in rows]
        sizes = [max(len(row[i]) for row in rows) for i in range(width)]

        def line(cells):
            return "  ".join(cell.ljust(size) for cell, size in zip(cells, sizes)).rstrip()

        out.extend([line(rows[0]), "  ".join("-" * size for size in sizes),
                    *(line(row) for row in rows[1:])])
        rows.clear()

    for row in text.split("\n"):
        if row.startswith("|"):
            cells = [_ESCAPED.sub(r"\1", cell.strip()) for cell in _CELL.split(row.strip())[1:-1]]
            if not (cells and all(cell and set(cell) <= {"-", ":"} for cell in cells)):
                rows.append(cells)  # the separator row is left out
        else:
            flush()
            out.append(_ESCAPED.sub(r"\1", row))
    flush()
    return "\n".join(out)


def _by_folder(paths):
    """{folder: [names in it]}, in order."""
    by_folder = {}
    for path in paths:
        folder, name = os.path.split(path)
        by_folder.setdefault(folder, []).append(name)
    return by_folder


def _scanned(paths):
    """Which skills ran and where, so the user sees where a name or relative path led:
    a line per folder, or one line when they come from more than three folders."""
    by_folder = _by_folder([os.path.abspath(path) for path in paths])
    if len(by_folder) <= 3:
        return "\n".join(f"Scanned {', '.join(names)} in {folder}"
                         for folder, names in by_folder.items())
    try:
        return f"Scanned {len(paths)} skills in {os.path.commonpath(list(by_folder))}"
    except ValueError:  # on different drives
        return f"Scanned {len(paths)} skills"


def _saved(artifacts):
    """One line per folder written to, rather than one per file."""
    return [f"Saved {', '.join(names)} to {folder or '.'}"
            for folder, names in _by_folder(artifacts).items()]


def _summary(result, paths=()):
    """A response as text for the user. Like the response, it holds no detected values.
    paths are the skills that ran."""
    if result["status"] == "error":
        lines = [f"metaskill {result['operation']} failed: {result['error']}"]
    else:
        lines = [_scanned(paths)] if paths else []
        lines.append(_plain(result["table"]) if result.get("table")
                     else f"metaskill {result['operation']} finished.")
    lines += [f"Warning: {warning}" for warning in result.get("warnings", [])]
    for missing in result.get("dependencies", []):
        how = missing["install_command"] or missing["install_url"] or missing.get("reason")
        lines.append(f"Not installed: {missing['package']}" + (f" ({how})" if how else ""))
    if result.get("unresolved"):
        lines.append(f"Unresolved: {len(result['unresolved'])}")
    lines += _saved(result.get("artifacts", []))
    return "\n".join(lines)


def make_command_handler(ctx):
    """Build the /metaskill handler.

    An operation runs here and prints its result, or says how to name the skill.
    `skills` lists the skills it can find. Anything else goes to the agent as a user
    message. Hermes only allows that for a plugin with allow_gateway_injection, and a
    classic CLI session does not show the agent's reply. Every path returns text: Hermes
    prints nothing for a handler that returns None.
    """

    def handle_command(raw_args):
        # Hermes prints this as ANSI text. Folder names and paths come from disk, and on
        # Linux or macOS a folder name can hold an escape sequence.
        return _printable(respond(raw_args))

    def respond(raw_args):
        request = (raw_args or "").strip()
        try:
            cwd = _session_cwd(ctx)
            where = _where(cwd or os.getcwd())
            if not request:
                return f"{USAGE}\n{where}"
            command, _, rest = request.partition(" ")
            if command == "skills":
                return _list_skills(rest, cwd or os.getcwd())
            if request in _ENTRY_POINTS:
                return f"metaskill {request}: which skill?\n{SKILL_HELP}\n{where}"
            parsed = _parse_command(request)
            if parsed:
                operation, skills, config_path = parsed
                paths, problems = _find_skills(operation, skills, cwd)
                if problems or not paths:
                    return "\n".join([*problems, SKILL_HELP, where])
                target = paths if operation == "rank" else paths[0]
                result = run(operation, target, config_path=config_path, ctx=ctx)
                return _summary(result, paths)
            if not ctx.inject_message(f"Use the metaskill tool to: {request}"):
                return ("metaskill: could not send the request to the agent. Set "
                        "plugins.entries.metaskill.allow_gateway_injection: true in "
                        f"config.yaml to allow that, or use a command.\n{USAGE}")
            return ("metaskill: sent to the agent. A classic CLI session (hermes chat) does "
                    "not show its reply.")
        except Exception as exc:  # report the type only: messages may echo file contents
            return f"metaskill failed: {type(exc).__name__}"

    return handle_command
