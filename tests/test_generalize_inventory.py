"""File discovery in metaskill/generalize/inventory.py."""

import os

import pytest

from metaskill.generalize.inventory import InventoryError, SkipReason, load_inventory


def write(root, files: dict[str, bytes | str]):
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode() if isinstance(content, str) else content
        path.write_bytes(data)
    return root


def test_reads_eligible_files_in_sorted_order(tmp_path):
    write(tmp_path, {
        "SKILL.md": "# s",
        "scripts/run.py": "print(1)",
        "scripts/run.sh": "echo",
        "scripts/client.js": "x",
        "references/api.md": "x",
        "templates/config.yaml": "a: 1",
        "templates/config.yml": "a: 1",
        "data.json": "{}",
        ".env": "A=1",
        ".env.local": "A=1",
        "Dockerfile": "FROM python",
        "Makefile": "all:",
        "bin/deploy": "#!/bin/sh\necho",
    })
    inventory = load_inventory(tmp_path)
    assert [f.path for f in inventory.files] == [
        ".env", ".env.local", "Dockerfile", "Makefile", "SKILL.md", "bin/deploy",
        "data.json", "references/api.md", "scripts/client.js", "scripts/run.py",
        "scripts/run.sh", "templates/config.yaml", "templates/config.yml",
    ]
    assert inventory.skipped == []


def test_skips_unsupported_binary_non_utf8_and_large_files(tmp_path):
    write(tmp_path, {
        "SKILL.md": "# s",
        "assets/logo.png": b"\x89PNG",
        "notes.txt": b"text\x00with nul",
        "latin1.md": "caf\u00e9".encode("latin-1"),
        "big.md": "x" * 101,
        "LICENSE": "MIT",  # extensionless without a shebang
    })
    inventory = load_inventory(tmp_path, max_file_bytes=100)
    assert [f.path for f in inventory.files] == ["SKILL.md"]
    assert {(s.path, s.reason) for s in inventory.skipped} == {
        ("LICENSE", SkipReason.UNSUPPORTED),
        ("assets/logo.png", SkipReason.UNSUPPORTED),
        ("big.md", SkipReason.TOO_LARGE),
        ("latin1.md", SkipReason.NOT_UTF8),
        ("notes.txt", SkipReason.BINARY),
    }


def test_ignores_version_control_and_cache_directories(tmp_path):
    write(tmp_path, {
        "SKILL.md": "# s",
        ".git/config": "[remote]\nurl = git@github.com:jayant/x.git",
        "scripts/__pycache__/run.cpython-312.pyc": b"\x00",
        "node_modules/pkg/index.js": "x",
    })
    inventory = load_inventory(tmp_path)
    assert [f.path for f in inventory.files] == ["SKILL.md"]
    assert inventory.skipped == []


def test_symlinks_are_skipped_not_followed(tmp_path):
    outside = write(tmp_path / "outside", {"secret.md": "jayant@company.com"})
    skill = write(tmp_path / "skill", {"SKILL.md": "# s"})
    os.symlink(outside / "secret.md", skill / "linked.md")
    os.symlink(outside, skill / "linked_dir")
    inventory = load_inventory(skill)
    assert [f.path for f in inventory.files] == ["SKILL.md"]
    assert {(s.path, s.reason) for s in inventory.skipped} == {
        ("linked.md", SkipReason.SYMLINK),
        ("linked_dir", SkipReason.SYMLINK),
    }


def test_standalone_skill_md(tmp_path):
    write(tmp_path, {"SKILL.md": "# s", "other.md": "x"})
    inventory = load_inventory(tmp_path / "SKILL.md")
    assert inventory.root == tmp_path
    assert [f.path for f in inventory.files] == ["SKILL.md"]


def test_too_many_files_is_rejected(tmp_path):
    write(tmp_path, {f"r/{i}.md": "x" for i in range(4)})
    with pytest.raises(InventoryError, match="eligible files"):
        load_inventory(tmp_path, max_files=3)


def test_too_many_bytes_is_rejected(tmp_path):
    write(tmp_path, {"a.md": "x" * 60, "b.md": "x" * 60})
    with pytest.raises(InventoryError, match="bytes"):
        load_inventory(tmp_path, max_total_bytes=100)


def test_missing_path(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_inventory(tmp_path / "nope")
