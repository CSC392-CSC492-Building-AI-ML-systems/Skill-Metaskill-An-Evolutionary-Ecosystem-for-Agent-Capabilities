"""File discovery in metaskill/generalize/inventory.py."""

import os

import pytest

from metaskill.generalize.inventory import InventoryError, SkipReason, SkippedFile, load_inventory


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


@pytest.mark.skipif(
    os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="permission test is unsupported on Windows or running as root",
)
def test_unreadable_files_and_directories_are_skipped_not_fatal(tmp_path):
    write(tmp_path, {"SKILL.md": "# s", "secret.md": "x", "locked/inner.md": "x"})
    (tmp_path / "secret.md").chmod(0)
    (tmp_path / "locked").chmod(0)
    try:
        inventory = load_inventory(tmp_path)
    finally:
        (tmp_path / "secret.md").chmod(0o644)
        (tmp_path / "locked").chmod(0o755)
    assert [f.path for f in inventory.files] == ["SKILL.md"]
    assert {(s.path, s.reason) for s in inventory.skipped} == {
        ("locked", SkipReason.UNREADABLE),
        ("secret.md", SkipReason.UNREADABLE),
    }


def test_standalone_skill_md(tmp_path):
    write(tmp_path, {"SKILL.md": "# s", "other.md": "x"})
    inventory = load_inventory(tmp_path / "SKILL.md")
    assert inventory.root == tmp_path
    assert [f.path for f in inventory.files] == ["SKILL.md"]


def test_extracts_skill_metadata_prompt_tools_references_and_dependencies(tmp_path):
    write(tmp_path, {
        "SKILL.md": (
            "---\n"
            "name: sample-skill\n"
            "description: |\n"
            "  Helps with sample work.\n"
            "tools:\n"
            "  - name: browser\n"
            "  - shell\n"
            "---\n"
            "Use the browser tool. See [guide](references/guide.md).\n"
            "Read API key from os.getenv('SERVICE_KEY').\n"
        ),
        "requirements.txt": "requests>=2.0\n# ignored\n",
        "package.json": '{"dependencies":{"chalk":"^5.0.0"}}',
        "scripts/run.py": "import json\nfrom pathlib import Path\n",
        "references/guide.md": "Guide",
        "assets/icon.png": b"\x89PNG",
    })
    inventory = load_inventory(tmp_path)

    assert inventory.project.metadata["name"] == "sample-skill"
    assert inventory.project.metadata["description"] == "Helps with sample work."
    assert inventory.project.prompt.startswith("Use the browser tool.")
    assert inventory.project.tools == ["browser", "shell"]
    assert "requests>=2.0" in inventory.project.dependencies
    assert "chalk@^5.0.0" in inventory.project.dependencies
    assert "json" not in inventory.project.dependencies
    assert "pathlib" not in inventory.project.dependencies
    assert "json" not in inventory.project.imported_modules
    assert "pathlib" not in inventory.project.imported_modules
    assert inventory.project.referenced_files == ["references/guide.md"]
    assert inventory.project.environment_variables == ["SERVICE_KEY"]
    assert inventory.skipped == [SkippedFile("assets/icon.png", SkipReason.UNSUPPORTED)]


def test_inventory_never_executes_skill_files(tmp_path):
    write(tmp_path, {
        "SKILL.md": "---\nname: safe\n---\nRun scripts/unsafe.py\n",
        "scripts/unsafe.py": "raise RuntimeError('must not execute')\n",
    })
    inventory = load_inventory(tmp_path)
    assert "scripts/unsafe.py" in [item.path for item in inventory.files]
    assert inventory.project.metadata["name"] == "safe"


def test_too_many_files_is_rejected(tmp_path):
    write(tmp_path, {f"r/{i}.md": "x" for i in range(4)})
    with pytest.raises(InventoryError, match="file limit"):
        load_inventory(tmp_path, max_files=3)


def test_file_count_limit_includes_unsupported_files(tmp_path):
    write(tmp_path, {f"assets/{i}.png": b"\x89PNG" for i in range(4)})
    with pytest.raises(InventoryError, match="file limit"):
        load_inventory(tmp_path, max_files=3)


def test_too_many_bytes_is_rejected(tmp_path):
    write(tmp_path, {"a.md": "x" * 60, "b.md": "x" * 60})
    with pytest.raises(InventoryError, match="bytes"):
        load_inventory(tmp_path, max_total_bytes=100)


def test_missing_path(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_inventory(tmp_path / "nope")


def test_nested_hermes_metadata_and_multiline_blocks_are_preserved(tmp_path):
    write(tmp_path, {"SKILL.md": (
        "---\n"
        "name: nested-skill\n"
        "description: |\n"
        "  First paragraph.\n"
        "\n"
        "  Second paragraph.\n"
        "metadata:\n"
        "  hermes:\n"
        "    tags: [github, gh, git]\n"
        "    category: software-development\n"
        "    tools:\n"
        "      - name: browser\n"
        "---\n"
        "Prompt body.\n"
    )})
    project = load_inventory(tmp_path).project
    assert project.metadata["metadata"]["hermes"]["tags"] == ["github", "gh", "git"]
    assert project.metadata["metadata"]["hermes"]["category"] == "software-development"
    assert project.metadata["description"] == "First paragraph.\n\nSecond paragraph."
    assert project.tools == ["browser"]


def test_imports_are_language_specific_and_standard_library_is_not_a_dependency(tmp_path):
    write(tmp_path, {
        "run.py": "import json\nimport requests\n",
        "run.js": "import React from \"react\";\n",
        "requirements.txt": "requests>=2.0\n",
    })
    project = load_inventory(tmp_path).project
    assert project.dependencies == ["requests>=2.0"]
    assert project.imported_modules == ["react", "requests"]


def test_body_tool_scan_requires_tools_heading(tmp_path):
    write(tmp_path, {"SKILL.md": (
        "---\nname: prose\n---\n"
        "Tool: use the browser carefully.\n"
        "## Tools\n"
        "- browser\n"
        "- shell\n"
        "## Notes\n"
        "- not a tool\n"
    )})
    assert load_inventory(tmp_path).project.tools == ["browser", "shell"]


def test_bracket_environment_variables_and_skill_md_only_references(tmp_path):
    write(tmp_path, {
        "SKILL.md": (
            "---\nname: refs\n---\n"
            "See [guide](docs/guide.md).\n"
            "Use os.environ[\"API_KEY\"] and process.env[\"NODE_KEY\"].\n"
        ),
        "other.md": "See [unrelated](other-target.md).\n",
    })
    project = load_inventory(tmp_path).project
    assert project.environment_variables == ["API_KEY", "NODE_KEY"]
    assert project.referenced_files == ["docs/guide.md"]


def test_poetry_dependencies_are_read_and_credential_lines_are_not_exposed(tmp_path):
    secret = "https://user:supersecret@example.com/repo.git"
    write(tmp_path, {
        "pyproject.toml": (
            "[tool.poetry.dependencies]\n"
            "python = \"^3.11\"\n"
            "requests = \"^2.31\"\n"
            f"private = \"{secret}\"\n"
        ),
        "requirements.txt": f"safe-package>=1.0\n{secret}\n",
    })
    project = load_inventory(tmp_path).project
    assert "requests^2.31" in project.dependencies
    assert "python^3.11" not in project.dependencies
    assert all("supersecret" not in dependency for dependency in project.dependencies)
    assert "supersecret" not in repr(project)
