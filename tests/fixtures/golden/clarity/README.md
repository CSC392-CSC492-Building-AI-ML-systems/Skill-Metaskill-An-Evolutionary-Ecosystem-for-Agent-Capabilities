# Clarity golden dataset (draft)

**Status: temporary, unreviewed.** Ten synthetic skills written as *positive controls* for the Clarity dimension: each should score high. They are for the evaluation team to analyse before the rubric and schema are agreed (see [golden_dataset.md](../../../../docs/evaluation/golden_dataset.md)).

## Criteria

A skill has high clarity when it is:

1. **Concise** (`conciseness`): says what it needs to and stops.
2. **Accessible** (`accessible_language`): a reader with basic subject knowledge can follow it; jargon and scientific names are avoided or explained.
3. **Readable** (`readability`): a person can tell what the skill accomplishes without an AI explaining it.
4. **Well framed and structured** (`problem_and_structure`): the problem is stated clearly, sections are well organised, and the writing flows.

## Layout

```text
cases.json          one case per skill: expected properties, verbatim evidence quotes, rationale
skills/<name>/      the skill itself (SKILL.md, Hermes front matter)
```

Annotations live outside the skill folders so a judge reading a skill never sees its expected label.

## Skills

| Skill | What it tests beyond the baseline |
|---|---|
| csv-dedupe | Inline script that should not count as wordiness |
| git-undo-last-commit | Risky operation explained without jargon |
| meeting-action-items | Pure writing task, no tools |
| backyard-bird-log | Jargon-prone domain; common names over scientific names |
| image-resize-web | Prerequisite section before steps |
| pdf-merge | Very short; brevity should not be penalised |
| ab-test-readout | Unavoidable technical terms, each defined at first use |
| recipe-scaling | Failure cases under a non-standard heading |
| markdown-link-check | Longest script; second code-length check |
| monthly-spending-review | Domain term (debit) used once where the source data uses it |

`tests/test_golden_clarity.py` checks that every quote exists verbatim in its skill and every case covers all four criteria.
