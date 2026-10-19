# Safety implementation plan

Status: planning. One document per file in `metaskill/evaluate/safety/`. The overall design is in [evaluate-safety.md](../evaluate-safety.md); these documents describe how each file implements it.

| File | Document | State |
|---|---|---|
| `findings.py` | [findings.md](findings.md) | Built, tested |
| `cisco.py` | [cisco.md](cisco.md) | Built, tested |
| `__init__.py` | [score.md](score.md) | Planned |
| `checks.py` | [checks.md](checks.md) | Planned |
| `review.py` | [review.md](review.md) | Planned; blocked on `judge.py` |

## How the files fit together

```
runner ──► safety.score(skill, config)                    __init__.py
              │
              ├─ cisco.py    CiscoBackend.scan()    ─► BackendResult
              ├─ checks.py   ChecksBackend.scan()   ─► BackendResult
              └─ review.py   ReviewBackend.scan()   ─► BackendResult   (deep only)
              │
              ├─ merge(findings)                                       findings.py
              ├─ score_from_findings(findings)                         findings.py
              └─ status from the backend statuses
              │
              ▼
         DimensionScore(value, status, reason, detail, findings)
```

Each backend is independent: it only knows how to scan a skill and return a `BackendResult`. `__init__.py` is the only file that knows about all of them.

## Build order

1. `findings.py`: shared types and logic. Done.
2. `cisco.py`: gives a working `static` tier with one backend.
3. `__init__.py`: wires backends together; `score()` is usable by the runner from here.
4. `checks.py`: adds the careless categories no scanner covers.
5. `review.py`: adds `excess-scope`; needs `judge.py`.

## Rules every backend follows

- **Never raise.** Every failure becomes a `BackendResult` status. One broken backend must not stop the evaluation.
- **Report the tier that actually ran**, not the one requested.
- **Locations** are `path:line` relative to the skill directory (`SKILL.md:42`, `scripts/run.sh:7`), `path` when there is no line, and empty for findings about the whole skill. The golden dataset will match on this.
- **Categories** come only from section 2 of the safety design: our four, or Cisco's own names unchanged.
- **Quotes** are verbatim text from the skill. Nothing is paraphrased.
- **Only `__init__.py` decides the score.** Backends report findings; they do not score.

## Decisions needed

Collected from the documents below. Each is explained where it appears.

| # | Decision | Where | Proposed |
|---|---|---|---|
| 1 | `scan()` takes a loaded `Skill` instead of a directory | [findings.md](findings.md) | **Decided: yes** |
| 2 | Scanner notices (`LLM_ANALYSIS_FAILED`, `LLM_CONTEXT_BUDGET_EXCEEDED`) go in `detail`, not findings | [cisco.md](cisco.md) | **Decided: yes** |
| 3 | Cisco's `hardcoded_secrets` and `policy_violation` findings | [cisco.md](cisco.md) | **Decided:** routed to Configuration Cleanliness as evidence, not scored in safety |
| 4 | Status when only some backends ran | [score.md](score.md) | Table in score.md |
| 5 | Own checks only read code blocks and scripts, not prose | [checks.md](checks.md) | Yes |
| 6 | Severity per own-check rule | [checks.md](checks.md) | Table in checks.md |
| 7 | Review asks about all four of our categories, not only `excess-scope` | [review.md](review.md) | Yes |
