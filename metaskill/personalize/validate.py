"""Output checks: where personalization may write (design.md, section 4)."""

from __future__ import annotations

from pathlib import Path

from .config import PersonalizeError


def check_output_dir(output: Path, source: Path) -> None:
    """Refuse an output that would overwrite something or touch the generalized input.

    An existing directory is accepted only when empty, so a rerun never replaces an
    earlier result. Overlap is checked on resolved paths, so a symlinked parent cannot
    point the output back into the input.
    """
    if output.is_symlink():
        raise PersonalizeError(["output path: refusing to write through a symlink"])
    target, root = output.resolve(), source.resolve()
    if target.is_relative_to(root) or root.is_relative_to(target):
        raise PersonalizeError(["output path: must be outside the generalized skill"])
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise PersonalizeError(["output path: already exists and is not an empty directory"])


def destination(root: Path, relative: str) -> Path:
    """The path to write a template file to, which must stay inside root."""
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise PersonalizeError([f"{relative}: would be written outside the output directory"])
    return path
