# Skill Evaluation: Safety (3.6) — Design

Status: draft. Items marked **[OPEN]** need a decision before implementation. Items marked **[VERIFY]** are taken from a scanner's README or docs and have not been checked by running the tool.

This expands section 3.6 of [evaluate.md](evaluate.md). Code lives in `metaskill/evaluate/safety/`.

## 1. Purpose

Answer one question per skill: "Does this skill ask the agent to do something that could harm the user?"

A skill can be unsafe in two different ways, and they need different detection:

- **Malicious**: the author intends harm. Prompt injection, data exfiltration, hidden instructions, poisoned dependencies.
- **Careless**: the author means well but the instructions are risky. An unconfirmed `rm -rf`, a printed API key, a `curl | sh` install.

Existing scanners target the malicious case and miss most of the careless one. This component covers both. The careless layer is our own checks. The malicious layer in this draft is an external scanner.

**[OPEN]** Whether the malicious layer needs our own implementation instead of, or in addition to, an external scanner. Sections 3.1 and 4 describe the external-scanner option.

## 2. What is flagged

| Category | Example | Detected by |
|---|---|---|
| `destructive-unconfirmed` | Recursive delete, force push, database drop with no confirmation step | Own checks |
| `credential-logging` | Instructions that make the agent print or log the user's credentials at run time | Own checks |
| `untrusted-source` | Unpinned install, `curl \| sh`, known-vulnerable dependency | Own checks + scanner |
| `excess-scope` | Broad permissions, or side effects outside the skill's stated purpose | LLM review |
| `injection` | Prompt injection, jailbreak text, hidden or obfuscated instructions | Scanner |
| `exfiltration` | Reading data and sending it out | Scanner |
| `malicious-code` | Unsafe execution primitives, command injection, malware signatures | Scanner |

`destructive-unconfirmed` is the category no existing scanner covers, so it is the part of this component that is new.

Personal config, hardcoded secrets and placeholders left behind by `generalize` are not scored here. They make a skill unclean, not unsafe to run, and Configuration Cleanliness (3.4 of evaluate.md) already penalizes them. A skill that sends credentials to a third party is covered by `exfiltration`.

**[OPEN]** The scanner reports hardcoded secrets as its own category, so its findings will include them. To discuss: the adapter can drop those findings by filtering on category, pass them to Configuration Cleanliness, or keep them in the safety evidence without letting them affect the score.

## 3. Backends

### 3.1 Cisco skill-scanner (primary)

`pip install cisco-ai-skill-scanner`, pinned to an exact version (2.2.1 at time of writing **[VERIFY]**). Requires Python 3.11+ **[VERIFY]**.

Chosen because it installs as a normal pinned dependency, has a Python SDK (no subprocess or output parsing), accepts a skill directory or a directory of skills, and publishes accuracy numbers.

Used through the SDK:

```python
from skill_scanner import SkillScanner
result = SkillScanner(analyzers=[...]).scan_skill("/path/to/skill")
```

It is an optional dependency. If it is not installed, the backend is skipped and the report says "scanner not installed"; evaluation does not fail.

**[OPEN]** Do Hermes skills parse as-is? The scanner expects `SKILL.md` per the Agent Skills spec. It also has a lenient mode and a custom metadata filename option for other layouts. Test on one real Hermes skill before writing the adapter.

### 3.2 Own checks

Deterministic rules over the skill's markdown and bundled scripts, for `destructive-unconfirmed`, `credential-logging` and `untrusted-source`.

- `destructive-unconfirmed`: match a list of destructive commands, then look for a confirmation step (ask, confirm, dry run, backup) in the same step or the one before it. A match with no confirmation is a finding.
- `credential-logging`: match commands that write a secret to the terminal or a log, such as echoing a key or token variable, dumping the environment, or printing a credentials file.

### 3.3 LLM review

One call through `judge.py`, asking about the three careless categories plus `excess-scope`, which needs the skill's stated purpose and cannot be done by pattern matching. The judge must quote the text behind each finding, and the quote is checked programmatically to be a real substring of the skill; findings with a quote that is not in the skill are dropped.

### 3.4 Considered: NVIDIA SkillSpector

Not included in the first version. It needs a source install with no releases or tags to pin, has no SDK, and publishes no accuracy numbers **[VERIFY]**. Its rule set is broader than Cisco's on permissions and tool access (excessive agency, least privilege, underdeclared capability).

**[OPEN]** Revisit if the validation set (section 7) shows Cisco plus our LLM review missing the `excess-scope` cases. The backend interface in section 5 allows adding it without other changes.

## 4. Tiers

| Tier | Runs | Needs a key | Cost |
|---|---|---|---|
| `static` (default) | Cisco static analyzers + own checks | No | None |
| `deep` | Everything in `static` + Cisco LLM analyzer + LLM review (3.3) | Yes | Two LLM passes per skill |

Static-only is not enough to rank on. Cisco reports that its rules alone catch 7.7% of malicious skills at HIGH on its benchmark, against 66.7% reaching MEDIUM or above with its LLM judge (15.4% false-positive rate) **[VERIFY]**. That benchmark covers malicious skills only and used one specific judge model, so it does not transfer directly to our setup or to the careless categories.

Rules:

- Every result records its tier.
- Ranking only compares skills scanned at the same tier, the same scanner version, and the same judge model. This extends the rule in section 5 of evaluate.md.
- A `static` result is displayed as "static only", not as a clean bill of health.
- If the LLM analyzer cannot start, Cisco's scan errors out instead of falling back. The adapter catches this, returns the static findings, and marks the result "LLM layer unavailable".

No cascade (escalating to the LLM only when static results look bad) in the first version. With static recall that low, most risky skills would never be escalated, and at our test-set size running the LLM tier on every skill is cheap compared with the task runs in section 4 of evaluate.md. Worth revisiting only for skill breeding, where the same skills are rescanned many times.

### Cost and privacy

- `deep` sends the full skill contents to the configured LLM provider. Before the first call, show which provider receives what. This matters most for a skill that has not been generalized yet and still contains personal config.
- **[OPEN]** Real token cost per scan is unknown. Measure it on one skill before setting a default cap.
- **[OPEN]** Cisco's LLM analyzer calls the model itself, with its own key and model settings. Section 6 of evaluate.md says scoring modules call `judge.py`, never a model directly. Either accept this as an exception and point Cisco at the same provider and model as `judge.py`, or skip Cisco's LLM analyzer and rely on our own review, losing its benchmarked detection.

## 5. Data model

Each backend implements one interface and returns normalized findings.

```python
class SafetyBackend(Protocol):
    name: str
    def available(self) -> bool: ...
    def scan(self, skill_dir: Path, tier: Tier) -> BackendResult: ...
```

```python
@dataclass
class Finding:
    category: str         # one of the categories in section 2
    severity: Severity    # CRITICAL | HIGH | MEDIUM | LOW | INFO
    quoted_text: str      # the offending text, verbatim
    location: str         # file and line
    message: str
    source: str           # "cisco" | "own-checks" | "llm-review"
    source_category: str  # the backend's own category or rule id
    source_version: str   # package version, or our rule-set version

@dataclass
class BackendResult:
    backend: str
    status: str           # "ok" | "not-installed" | "llm-unavailable" | "error"
    tier: Tier
    findings: list[Finding]
```

- Severity uses Cisco's five levels as the common scale, since it is the only external scale we map from.
- Two findings with the same category and overlapping location are merged into one that lists both sources. This prevents the same problem being counted twice.

### Score

evaluate.md needs a number in `[0, 1]` per dimension for the leaderboard. The safety score is set by the **worst** finding, not the sum, so a skill is not penalized again each time a second backend reports the same issue.

| Worst finding | Score |
|---|---|
| None | 1.0 |
| LOW or INFO | 0.9 |
| MEDIUM | 0.6 |
| HIGH | 0.3 |
| CRITICAL | 0.0 |

**[OPEN]** These values are placeholders. Also open: whether a CRITICAL finding should exclude a skill from ranking instead of only lowering its score, since a weighted `overall` could otherwise rank an unsafe skill first.

The findings, with quotes, are always written to the evidence file alongside the score.

## 6. Module layout

Replaces the single `safety.py` in section 6 of evaluate.md.

```
metaskill/evaluate/safety/
  __init__.py    # score(skill, tier) -> DimensionScore
  findings.py    # Finding, BackendResult, severity scale, merge
  cisco.py       # Cisco backend: SDK call, category and severity mapping
  checks.py      # own deterministic checks (3.2)
  review.py      # LLM review via judge.py, quote verification (3.3)
```

## 7. Validation

This is how we know the component works, and the numbers go in the final report.

Test set, kept small:

- **Benign controls**: real skills from public repos, expected to produce no findings.
- **Risky variants**: 3 or 4 real skills, each with one deliberate flaw added. One variant per category in section 2: unconfirmed `rm -rf`, printed credential, `curl | sh`, hidden instruction, over-broad permissions.
- **Known-malicious examples**: any that ship with the scanner.

Measured per category, at each tier: detection rate and false-positive rate, for Cisco alone, own checks alone, and the combination.

## 8. Testing

- Own checks: fixture skills in `tests/`, one clean and one per flaw.
- Cisco adapter: tests run against recorded scanner output, so CI does not need the package installed. One test covers the "not installed" path and one the "LLM layer unavailable" path.
- LLM review: fake judge returning canned output, including a finding whose quote is not in the skill, to assert it is dropped.
- Real scanner and LLM runs are excluded from CI, as in section 8 of evaluate.md.

## 9. Limitations

- No findings does not mean safe. The scanner we use says the same about itself.
- Low static recall. Without `deep`, most malicious skills pass undetected.
- The LLM tier is not deterministic. Two runs can give different findings.
- The confirmation check in 3.2 is a heuristic. A confirmation step written far from the command, or phrased unusually, will be missed and reported as a finding.
- We read instructions, not behaviour. A skill that fetches its real instructions at run time looks clean.
- Cisco's published accuracy does not cover our careless categories; only our own validation set does, and it is small.

## 10. Open questions

1. Does the malicious layer need our own implementation? (1)
2. Do Hermes skills parse in Cisco's scanner without conversion? (3.1)
3. Cisco's LLM analyzer versus the "only `judge.py` calls a model" rule. (4)
4. Score values, and whether CRITICAL excludes a skill from ranking. (5)
5. Per-scan token cost and the default cap. (4)
6. Whether to add SkillSpector for `excess-scope`. (3.4)
7. What to do with the scanner's hardcoded-secret findings: drop, hand to Configuration Cleanliness, or keep as unscored evidence. (2)
8. Caching findings by content hash depends on the storage decision still open in evaluate.md.
