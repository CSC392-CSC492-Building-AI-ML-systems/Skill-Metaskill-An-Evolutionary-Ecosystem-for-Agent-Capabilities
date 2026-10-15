# findings.py

Status: built and tested (`tests/safety/test_findings.py`, 13 tests). Implements section 5 of [evaluate-safety.md](../evaluate-safety.md).

## Purpose

The shared types and pure logic that every other safety file uses. It does not scan anything and does not call a model, so it is fully testable without the scanner or an API key.

It answers three questions:

1. What does a backend return? (`BackendResult`, `BackendStatus`)
2. What must a backend provide? (`SafetyBackend`)
3. Given everyone's findings, what is the safety score? (`merge`, `score_from_findings`)

## Contents

### Types

**`BackendStatus`**: how one backend's run went.

| Value | Meaning |
|---|---|
| `ok` | Ran as requested |
| `not-installed` | The backend's dependency is missing (Cisco not installed) |
| `llm-unavailable` | `deep` was requested but the LLM part could not run; static results only |
| `error` | Anything else went wrong; `detail` has the message |

This is per backend. The dimension-level `Status` in `models.py` (`ok`, `partial`, `skipped`, `error`) is derived from these in `__init__.py` (see [score.md](score.md)).

**`BackendResult`**: everything one backend returns for one skill.

| Field | Meaning |
|---|---|
| `backend` | `"cisco"`, `"own-checks"` or `"llm-review"` |
| `status` | A `BackendStatus` |
| `tier` | The tier that actually ran, which can be lower than requested |
| `findings` | Our `Finding` objects from `models.py` |
| `detail` | Explanation when `status` is not `ok`, or notes such as skipped files |

**`SafetyBackend`**: the interface each backend implements. A `Protocol`, so backends do not inherit from it; any class with these members qualifies.

```python
class SafetyBackend(Protocol):
    name: str
    def available(self) -> bool: ...
    def scan(self, skill_dir: Path, tier: SafetyTier) -> BackendResult: ...
```

### Functions

**`merge(findings)`**: combines findings that describe the same problem. Two findings are the same problem when they have the same dimension, the same or overlapping category, and the same location. The merged finding keeps the most severe one's fields, lists every source and rule once (`"own-checks, cisco"`), and fills an empty quote or message from the other. Output is ordered most severe first.

Overlapping categories, from section 2.1 of the safety design:

- `untrusted-source` and Cisco's `supply_chain_attack`
- `excess-scope` and Cisco's `unauthorized_tool_use` and `autonomy_abuse`

**`worst_severity(findings)`**: the most severe finding that counts toward safety, or `None`. Findings whose `dimension` is not safety are ignored, so findings routed to Configuration Cleanliness never lower the safety score.

**`score_from_findings(findings)`**: the worst-finding table. No safety findings scores 1.0.

| Worst finding | Score |
|---|---|
| None | 1.0 |
| LOW, INFO | 0.9 |
| MEDIUM | 0.6 |
| HIGH | 0.3 |
| CRITICAL | 0.0 |

Values are placeholders (`[OPEN]` in the safety design).

Important: 1.0 means "the backends ran and found nothing". It must never be returned when no backend ran. That check belongs in `__init__.py`, which looks at backend statuses before calling this.

## Decisions made while building

- Overlapping categories merge, following section 2.1, although section 5 says "same category".
- Locations match exactly after removing a leading `./`. Ranges are not handled: lines 40 to 45 do not merge with line 42.
- Merged sources and rules are stored as one comma-separated string, because `Finding.source` and `Finding.rule` are strings in `models.py`.

## Proposed change

**`scan()` should take a loaded `Skill`, not a directory.** Own checks and the LLM review need the file contents, and the runner already loads each skill once with `Skill.load()`. Passing the `Skill` avoids each backend reading the files again. Cisco uses `skill.path`.

```python
def scan(self, skill: Skill, tier: SafetyTier) -> BackendResult: ...
```

This is a one-line change to the protocol and the safety design's section 5.

## Tests

`tests/safety/test_findings.py` covers: empty input, worst finding wins, repeated findings do not lower the score further, other dimensions ignored, merging by category, overlapping category and location, not merging on different location, category or dimension, `./` prefix, filling empty quote and message, listing each source once, and ordering.
