"""Links that code reading a skill must not follow: symlinks and, on Windows, directory
junctions, which Path.is_symlink() does not report and os.walk(followlinks=False) walks
into. Standard library only.
"""

from __future__ import annotations

import os
from pathlib import Path

# Python 3.12 and later; before it, through_link's containment check still catches a
# junction that leads out of the folder.
_isjunction = getattr(os.path, "isjunction", lambda path: False)


def is_link(path: str | os.PathLike) -> bool:
    """Whether `path` is a symlink or a Windows junction. Other reparse points, such as a
    cloud file not yet downloaded, are the file itself, so they are read like any file."""
    return os.path.islink(path) or _isjunction(path)


def through_link(root: str | os.PathLike, path: str | os.PathLike) -> bool:
    """Whether `path`, a path under the folder `root`, is a link, is reached through one
    below `root`, or leads outside `root`. `root` itself may be reached through a link."""
    root, path = Path(root), Path(path)
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    current = root
    for part in parts:
        current /= part
        if is_link(current):
            return True
    real_root = os.path.realpath(root)
    try:
        return os.path.commonpath([real_root, os.path.realpath(path)]) != real_root
    except ValueError:  # on different drives (Windows)
        return True
