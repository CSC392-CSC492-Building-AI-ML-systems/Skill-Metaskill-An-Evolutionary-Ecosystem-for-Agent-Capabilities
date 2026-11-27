# Hub skills (test fixtures)

17 skills from three of the sources the Hermes skills hub installs from by default
(`GitHubSource.DEFAULT_TAPS` in Hermes), copied unchanged as real-skill test data beside
[hermes-skills](../hermes-skills). Load them with `tests/hub_skills.py` and
`hermes_skills.load`. They were chosen to differ from one another and to stay small
(about 1.7 MB), not to be a complete or representative copy of any source.

| Folder | Source | Commit | Licence |
|---|---|---|---|
| `anthropics/` | [anthropics/skills](https://github.com/anthropics/skills), `skills/` | `e4bb5d960d87126ff3b3a8af1a104b7db67560ba` (16 Nov 2026) | Apache-2.0, `LICENSE.txt` in each skill; [THIRD_PARTY_NOTICES.md](anthropics/THIRD_PARTY_NOTICES.md) is the repository's |
| `openai/` | [openai/skills](https://github.com/openai/skills), `skills/.curated/` and `skills/.system/` | `49f948faa9258a0c61caceaf225e179651397431` (24 Jun 2026) | Per skill, `LICENSE.txt` in each: MIT for `notion-meeting-intelligence` and `vercel-deploy`, Apache-2.0 for the rest |
| `huggingface/` | [huggingface/skills](https://github.com/huggingface/skills), `skills/` | `aafb3adfb80951cc90836cc6fd17bb345aa49eef` (26 Nov 2026) | Apache-2.0, [LICENSE](huggingface/LICENSE) |

| Skill | What it adds |
|---|---|
| `anthropics/academy-guide` | A description written over several lines of frontmatter |
| `anthropics/mcp-builder` | Python and XML beside long reference documents |
| `anthropics/pdf` | Python scripts that work on documents |
| `anthropics/skill-creator` | Many Python scripts and HTML, over several folders |
| `anthropics/web-artifacts-builder` | Shell scripts and a bundled `.tar.gz` |
| `anthropics/webapp-testing` | Python helpers and examples that start servers |
| `openai/gh-fix-ci` | A script that calls the GitHub CLI, with `agents/` YAML and images |
| `openai/jupyter-notebook` | Notebook (`.ipynb`) templates |
| `openai/notion-meeting-intelligence` | MIT licence; JSON evaluations and many references |
| `openai/screenshot` | PowerShell, Swift, shell and Python |
| `openai/security-ownership-map` | Larger Python scripts |
| `openai/skill-installer` | A system skill that downloads and installs other skills |
| `openai/vercel-deploy` | A deployment shell script |
| `huggingface/hf-cloud-sagemaker-iam-preflight` | IAM policy JSON with Python |
| `huggingface/hf-mem` | A single file |
| `huggingface/huggingface-spaces` | Many reference documents with install commands |
| `huggingface/transformers-js` | Extra frontmatter keys (`compatibility`, `metadata`) |

Each skill is the whole folder, byte for byte. To update one, copy the folder again from a
newer commit, change the commit above, then refresh the snapshot that depends on it.

These are third-party files used as test data. Do not edit them, and do not run their
scripts. Expected results live with the component that tests them: the own safety checks'
snapshot is `tests/safety/hub_expected.json`, checked by
`tests/safety/test_hub_skills.py` and refreshed with
`tests/safety/update_hermes_snapshot.py`. It records today's findings, which include
possible false positives: it shows change, not correctness.
