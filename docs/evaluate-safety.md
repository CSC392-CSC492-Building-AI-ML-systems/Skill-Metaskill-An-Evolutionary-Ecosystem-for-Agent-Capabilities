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

Every category a finding can have. A finding from our own checks or the LLM review uses one of the four categories in 2.1. A finding from the scanner keeps the scanner's own category name from 2.2.

### 2.1 Our categories

| Category | What is flagged | Detected by |
|---|---|---|
| `destructive-unconfirmed` | Recursive delete, force push, database drop or disk write with no confirmation step | Own checks |
| `credential-logging` | Instructions that make the agent print or log the user's credentials at run time | Own checks |
| `untrusted-source` | Unpinned install, `curl \| sh`, download from an unknown source | Own checks |
| `excess-scope` | Broad permissions, or side effects outside the skill's stated purpose | LLM review |

`destructive-unconfirmed` and `credential-logging` are covered by no scanner category, so they are the part of this component that is new. `untrusted-source` overlaps with the scanner's `supply_chain_attack`, and `excess-scope` with `unauthorized_tool_use` and `autonomy_abuse`; overlapping findings at the same location are merged (section 5).

### 2.2 Scanner categories

The complete list from Cisco skill-scanner 2.2.1, read from the installed package.

| Category | What is flagged | Scored under safety |
|---|---|---|
| `prompt_injection` | Text that overrides the agent's instructions, including jailbreak framing | Yes |
| `transitive_trust_abuse` | Indirect injection: the skill has the agent follow instructions from external content | Yes |
| `unicode_steganography` | Payloads hidden in invisible Unicode characters | Yes |
| `obfuscation` | Encoded or disguised content meant to evade review | Yes |
| `social_engineering` | Deceptive metadata or scam-like behaviour | Yes |
| `skill_discovery_abuse` | Inflated or misleading description of what the skill does | Yes |
| `data_exfiltration` | Reading data and sending it out, including outbound network requests | Yes |
| `tool_chaining_abuse` | A chain that collects data in one step and uploads it in another | Yes |
| `command_injection` | Unsafe execution primitives and injection patterns in bundled scripts | Yes |
| `malware` | Known malware signatures | Yes |
| `supply_chain_attack` | Malicious or unpinned packages, registry redirection, known-vulnerable dependencies | Yes |
| `unauthorized_tool_use` | Tool or network use the skill does not declare | Yes |
| `autonomy_abuse` | Unbounded autonomous retries or actions | Yes |
| `resource_abuse` | Compute exhaustion, such as infinite loops | Yes |
| `harmful_content` | Instructions to produce prohibited harmful content | Yes |
| `hardcoded_secrets` | Credentials embedded in the skill | **[OPEN]**, see below |
| `policy_violation` | Packaging rules: skill naming, description length, missing license, archive or binary files in the package | **[OPEN]**, see below |

Personal config, hardcoded secrets and placeholders left behind by `generalize` are not scored here. They make a skill unclean, not unsafe to run, and Configuration Cleanliness (3.4 of evaluate.md) already penalizes them. A skill that sends credentials to a third party is covered by `data_exfiltration`.

**[OPEN]** Two scanner categories are not about harm to the person running the skill: `hardcoded_secrets` is a cleanliness issue, and `policy_violation` is packaging hygiene. To discuss for each: the adapter can drop those findings by filtering on category, pass them to Configuration Cleanliness, or keep them in the safety evidence without letting them affect the score.

## 3. Backends

### 3.1 Cisco skill-scanner (primary)

`pip install cisco-ai-skill-scanner`, pinned to an exact version (2.2.1 at time of writing). Requires Python 3.11+ **[VERIFY]**; tested on 3.13.

Chosen because it installs as a normal pinned dependency, has a Python SDK (no subprocess or output parsing), accepts a skill directory or a directory of skills, and publishes accuracy numbers.

Used through the SDK:

```python
from skill_scanner import SkillScanner
result = SkillScanner(analyzers=[...]).scan_skill("/path/to/skill")
```

It is an optional dependency. If it is not installed, the backend is skipped and the report says "scanner not installed"; evaluation does not fail.

Hermes skills parse as-is, with no conversion and no lenient mode. Tested on 8 Oct 2026 with version 2.2.1 in static mode: all 59 skills in a local `~/.hermes/skills` were scanned without a parse error, through both the CLI (`scan-all --recursive`) and the SDK. Hermes-specific frontmatter (`platforms`, `metadata.hermes`) caused no problems. OpenClaw skills have not been tested.

Each finding returned by the SDK has `rule_id`, `category`, `severity`, `file_path`, `line_number`, `snippet`, `title`, `description`, `remediation` and `analyzer`. These map onto the `Finding` in section 5: `snippet` to `quoted_text`, `file_path` and `line_number` to `location`, and `category` and `rule_id` unchanged.

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
- Cisco's LLM analyzer calls the model itself. This is the one exception to the rule in section 6 of evaluate.md that scoring modules call `judge.py`, never a model directly. The adapter configures it with the same provider and model as `judge.py`.

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
    rule_id: str          # the backend's rule that fired
    source_version: str   # package version, or our rule-set version

@dataclass
class BackendResult:
    backend: str
    status: str           # "ok" | "not-installed" | "llm-unavailable" | "error"
    tier: Tier
    findings: list[Finding]
    detail: str = ""      # error message, when status is "error"
```

- Severity uses Cisco's five levels as the common scale, since it is the only external scale we map from.
- Two findings with the same category and overlapping location are merged into one that lists both sources. This prevents the same problem being counted twice.

### Cisco adapter

The adapter is the one module that knows about Cisco's scanner (`cisco.py`). It runs the scanner on a skill directory and translates what comes back into our `Finding` and `BackendResult`. Nothing else in `evaluate` imports `skill_scanner`, so the scanner can be upgraded, swapped or left uninstalled without touching scoring or ranking.

It does five things:

1. **Checks availability.** The import is guarded. If the package is missing, `available()` returns false and `scan()` returns `status="not-installed"` with no findings.
2. **Picks analyzers from the tier.** `static` uses the scanner's default analyzers, which need no key. `deep` adds the LLM analyzer, given the same provider, model and key that `judge.py` uses.
3. **Runs the scan** through the SDK on one skill directory.
4. **Translates findings.** Field by field, as in the table below.
5. **Handles failure.** If the `deep` scan raises because the LLM analyzer cannot start, it reruns as `static` and returns those findings with `status="llm-unavailable"`. Any other scanner error returns `status="error"` with the message; it never raises into the caller.

| Cisco finding | Our `Finding` | Note |
|---|---|---|
| `category` | `category` | Unchanged (section 2.2) |
| `severity` | `severity` | Same five levels |
| `snippet` | `quoted_text` | Can be empty, for findings about the skill as a whole |
| `file_path`, `line_number` | `location` | `line_number` can be missing |
| `title`, `description` | `message` | |
| `rule_id` | `rule_id` | |
| package version | `source_version` | From `importlib.metadata` |

Sketch:

```python
class CiscoBackend:
    name = "cisco"

    def available(self) -> bool:
        return importlib.util.find_spec("skill_scanner") is not None

    def scan(self, skill_dir: Path, tier: Tier) -> BackendResult:
        if not self.available():
            return BackendResult(self.name, "not-installed", tier, [])
        try:
            result = self._run(skill_dir, tier)
            status = "ok"
        except SkillScannerError as e:
            if tier is not Tier.DEEP:
                return BackendResult(self.name, "error", tier, [], detail=str(e))
            result = self._run(skill_dir, Tier.STATIC)
            status, tier = "llm-unavailable", Tier.STATIC
        return BackendResult(self.name, status, tier,
                             [self._convert(f) for f in result.findings])

    def _run(self, skill_dir: Path, tier: Tier):
        analyzers = default_analyzers()
        if tier is Tier.DEEP:
            analyzers.append(LLMAnalyzer(provider=..., model=..., api_key=...))
        return SkillScanner(analyzers=analyzers).scan_skill(skill_dir)
```

Checked against version 2.2.1: `SkillScanner(analyzers=...)`, `scan_skill(path)`, `LLMAnalyzer(provider, model, api_key, base_url, ...)`, the finding fields above, and a `SkillScannerError` base exception all exist. The scan result also has an `llm_usage` field, which is where the per-scan token cost can be read from.

**[VERIFY]** Which exception a `deep` scan raises when the LLM analyzer cannot start, and how to build the default analyzer list through the SDK. Neither has been run.

### Score

evaluate.md needs a number in `[0, 1]` per dimension for the leaderboard. The safety score is set by the **worst** finding, not the sum, so a skill is not penalized again each time a second backend reports the same issue.

| Worst finding | Score |
|---|---|
| None | 1.0 |
| LOW or INFO | 0.9 |
| MEDIUM | 0.6 |
| HIGH | 0.3 |
| CRITICAL | 0.0 |

A CRITICAL finding gives the lowest safety score. It does not remove the skill from the ranking (section 5 of evaluate.md).

**[OPEN]** These values are placeholders.

The findings, with quotes, are always written to the evidence file alongside the score.

## 6. Module layout

Expands the `safety/` entry in section 6 of evaluate.md.

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
- **Risky variants**: 3 or 4 real skills, each with one deliberate flaw added. One variant for each of our categories in 2.1, plus one scanner category: unconfirmed `rm -rf`, printed credential, `curl | sh`, hidden instruction, over-broad permissions.
- **Known-malicious examples**: any that ship with the scanner.

Measured per category, at each tier: detection rate and false-positive rate, for Cisco alone, own checks alone, and the combination.

## 8. Testing

- Own checks: fixture skills in `tests/`, one clean and one per flaw.
- Cisco adapter: tests run against recorded scanner output, so CI does not need the package installed. One test covers the "not installed" path and one the "LLM layer unavailable" path.
- LLM review: fake judge returning canned output, including a finding whose quote is not in the skill, to assert it is dropped.
- Real scanner and LLM runs are excluded from CI, as in section 8 of evaluate.md.

## 9. Limitations

- No findings does not mean safe. The scanner we use says the same about itself.
- Static false positives on ordinary skills. In the test in 3.1, 11 of the 59 installed Hermes skills had a HIGH or CRITICAL static finding. They have not been triaged, but at least one is a clear false positive: a CRITICAL "Function constructor" finding on a Playwright `page.waitForFunction('...')` call. With the worst-finding score in section 5, one such finding gives a skill the lowest score.
- Low static recall. Without `deep`, most malicious skills pass undetected.
- The LLM tier is not deterministic. Two runs can give different findings.
- The confirmation check in 3.2 is a heuristic. A confirmation step written far from the command, or phrased unusually, will be missed and reported as a finding.
- We read instructions, not behaviour. A skill that fetches its real instructions at run time looks clean.
- Cisco's published accuracy does not cover our careless categories; only our own validation set does, and it is small.

## 10. Open questions

1. Does the malicious layer need our own implementation? (1)
2. Do OpenClaw skills parse in Cisco's scanner without conversion? Hermes skills do. (3.1)
3. Score values. (5)
4. Per-scan token cost and the default cap. (4)
5. Whether to add SkillSpector for `excess-scope`. (3.4)
6. What to do with the scanner's `hardcoded_secrets` and `policy_violation` findings: drop, hand to Configuration Cleanliness, or keep as unscored evidence. (2)
7. Caching findings by content hash depends on the storage decision still open in evaluate.md.
