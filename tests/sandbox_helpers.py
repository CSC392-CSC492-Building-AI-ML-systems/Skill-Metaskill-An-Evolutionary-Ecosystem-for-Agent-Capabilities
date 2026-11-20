"""Shared inert sandbox used by runner and pilot tests."""

import shutil
import tempfile
from pathlib import Path

from metaskill.evaluate.sandbox import CommandResult


class FakeWorkspace:
    """A workspace that is a folder on this machine. Nothing runs in it: commands get
    their exit code from the sandbox's `code(folder)`. What goes anywhere but root is
    recorded in `skill`, as {folder: file contents}."""

    root = "/work"
    skill_dir = "/tmp/skill"

    def __init__(self, sandbox):
        self.sandbox, self.path = sandbox, Path(tempfile.mkdtemp(prefix="fake-workspace-"))
        self.closed, self.skill = False, {}

    def run_command(self, command, timeout, input=None):
        self.sandbox.commands.append((command, sorted(p.name for p in self.path.iterdir())))
        code = self.sandbox.code(self.path)
        return CommandResult(code, "", "" if code == 0 else "AssertionError")

    def put(self, paths, folder):
        if folder != self.root:
            self.skill.setdefault(folder, {}).update(
                {p.name: p.read_bytes() for p in paths})
            return
        for path in paths:
            if path.is_dir():
                shutil.copytree(path, self.path / path.name)
            else:
                shutil.copy2(path, self.path / path.name)

    def export(self, dest):
        shutil.copytree(self.path, dest, dirs_exist_ok=True)

    def close(self):
        shutil.rmtree(self.path, ignore_errors=True)
        self.closed = True


class FakeSandbox:
    """Stands in for DockerSandbox: `code(workdir)` gives the exit code of each command."""

    def __init__(self, code=lambda workdir: 0, reason="", disk="16m"):
        self.code, self.reason, self.disk = code, reason, disk
        self.asked, self.commands, self.opened = 0, [], []

    def check(self):
        return None

    def unavailable(self):
        self.asked += 1
        return self.reason

    def open(self, fixtures):
        workspace = FakeWorkspace(self)
        self.opened.append(workspace)
        try:
            workspace.put(fixtures, workspace.root)
        except BaseException:
            workspace.close()
            raise
        return workspace
