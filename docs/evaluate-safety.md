# Skill Evaluation: Safety (3.6) — Design

Status: draft. Items marked **[OPEN]** need a decision before implementation. Items marked **[VERIFY]** are taken from a scanner's README or docs and have not been checked by running the tool.

This expands section 3.6 of [evaluate.md](evaluate.md). Code lives in `metaskill/evaluate/safety/`; each file has its own document in [safety/](safety/README.md), which describes what was built. The `static` tier (Cisco and own checks) is implemented; the LLM review waits on `judge.py`.

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
| `excess-scope` | Broad permissions, or side effects outside the skill's stated purpose | LLM review; own checks for symlinks in the skill pointing outside it |

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
| `hardcoded_secrets` | Credentials embedded in the skill | No: routed to Configuration Cleanliness, see below |
| `policy_violation` | Packaging rules: skill naming, description length, missing license, archive or binary files in the package | No: routed to Configuration Cleanliness, see below |

Personal config, hardcoded secrets and placeholders left behind by `generalize` are not scored here. They make a skill unclean, not unsafe to run, and Configuration Cleanliness (3.4 of evaluate.md) already penalizes them. A skill that sends credentials to a third party is covered by `data_exfiltration`.

Two scanner categories are not about harm to the person running the skill: `hardcoded_secrets` is a cleanliness issue, and `policy_violation` is packaging hygiene. The adapter keeps them but sets their `dimension` to `config_cleanliness`. They stay in the report as evidence, never affect the safety score, and Configuration Cleanliness can use them or not.

The scanner's own diagnostic notices (`LLM_ANALYSIS_FAILED`, `LLM_CONTEXT_BUDGET_EXCEEDED`) also arrive as `policy_violation` findings. They describe the scan, not the skill, so they are not findings at all: the adapter moves them into the result's `detail` (see [safety/cisco.md](safety/cisco.md)).

## 3. Backends

### 3.1 Cisco skill-scanner (primary)

`pip install cisco-ai-skill-scanner`, pinned to an exact version (2.2.1 at time of writing). Requires Python 3.11 to 3.14 (from the package metadata); tested on 3.13.

Chosen because it installs as a normal pinned dependency, has a Python SDK (no subprocess or output parsing), accepts a skill directory or a directory of skills, and publishes accuracy numbers.

Used through the SDK:

```python
from skill_scanner import SkillScanner
result = SkillScanner(analyzers=[...]).scan_skill("/path/to/skill")
```

It is an optional dependency. When it is not installed:

1. **Ask before scanning.** The tool reports the missing scanner before any skill is evaluated, and the agent asks the user whether to install it, in the Python environment Hermes runs in, pinned to the tested version (`pip install cisco-ai-skill-scanner==2.2.1`). If the user agrees, it is installed and evaluation continues with Cisco. This follows how the agent asks which skills to rank (section 7 of evaluate.md).
2. **If the user declines, or there is no one to ask** (a non-interactive run), evaluation continues without Cisco. It does not fail.
   - In the report, safety has status `partial`, since our own checks still ran, and `detail` says the scanner is not installed and gives the install command.
   - In the tool's output, a warning is shown at the top of the result, because without Cisco most malicious skills go undetected (section 4).

Hermes skills parse as-is, with no conversion and no lenient mode. Tested on 8 Oct 2026 with version 2.2.1 in static mode: all 59 skills in a local `~/.hermes/skills` were scanned without a parse error, through both the CLI (`scan-all --recursive`) and the SDK. Hermes-specific frontmatter (`platforms`, `metadata.hermes`) caused no problems. OpenClaw skills have not been tested.

Each finding returned by the SDK has `rule_id`, `category`, `severity`, `file_path`, `line_number`, `snippet`, `title`, `description`, `remediation` and `analyzer`. These map onto the `Finding` in section 5: `snippet` to `quote`, `rule_id` to `rule`, `file_path` and `line_number` to `location`, and `category` unchanged.

### 3.2 Own checks

Deterministic rules over the skill's markdown and bundled scripts, for `destructive-unconfirmed`, `credential-logging` and `untrusted-source`. Everything is scanned, prose included. A match in prose that the same sentence warns against ("never run `rm -rf`") is skipped and listed in `detail` rather than reported. Full rules in [safety/checks.md](safety/checks.md).

- `destructive-unconfirmed`: match a list of destructive commands, then look for a confirmation step (ask, confirm, dry run, backup) in the same step or the one before it. A match with no confirmation is a finding.
- `credential-logging`: match commands that write a secret to the terminal or a log, such as echoing a key or token variable, dumping the environment, or printing a credentials file.

The rule set has a version number, bumped whenever a rule is added or changed, and recorded as `checks_version` in the report header. Changing a rule can change scores, so reports with different rule-set versions are not ranked together.

### 3.3 LLM review

One call through `judge.py`, asking about the three careless categories plus `excess-scope`, which needs the skill's stated purpose and cannot be done by pattern matching. The judge must quote the text behind each finding, and the quote is checked programmatically to be a real substring of the skill; findings with a quote that is not in the skill are dropped.

The review also re-checks the prose matches the own checks skipped as warnings, and can restore any that is a real instruction. It can only restore, never remove, so a prompt injection cannot make a skill look safer than the static result. Details and the restrictions on the call in [safety/review.md](safety/review.md).

### 3.4 Considered: NVIDIA SkillSpector

Not included in the first version. It needs a source install with no releases or tags to pin, has no SDK, and publishes no accuracy numbers **[VERIFY]**. Its rule set is broader than Cisco's on permissions and tool access (excessive agency, least privilege, underdeclared capability).

**[OPEN]** Revisit if the validation set (section 7) shows Cisco plus our LLM review missing the `excess-scope` cases. The backend interface in section 5 allows adding it without other changes.

## 4. Tiers

| Tier | Runs | Needs | Cost |
|---|---|---|---|
| `static` (default) | Cisco static analyzers + own checks | Nothing | None |
| `deep` | Everything in `static` + Cisco LLM analyzer + LLM review (3.3) | Hermes's `ctx.llm`, i.e. the user's configured model. No API key is ever handled by the plugin. | Two LLM passes per skill, plus one small call when own checks skipped warnings |

Static-only is not enough to rank on. Cisco reports that its rules alone catch 7.7% of malicious skills at HIGH on its benchmark, against 66.7% reaching MEDIUM or above with its LLM judge (15.4% false-positive rate) **[VERIFY]**. That benchmark covers malicious skills only and used one specific judge model, so it does not transfer directly to our setup or to the careless categories.

Rules:

- Every result records its tier.
- Every ranking is fresh, so all skills in it are scanned in the same run, at the same tier, with the same scanner version, own-checks version and judge model (section 5 of evaluate.md). A skill whose `deep` scan fell back to `static` is flagged in the ranking.
- A `static` result is displayed as "static only", not as a clean bill of health.
- If the LLM layer cannot run, the result keeps its static findings and is marked `llm-unavailable`, and safety's status is `partial`. Cisco fails in two different ways here (section 5, Cisco adapter); the adapter handles both.

No cascade (escalating to the LLM only when static results look bad) in the first version. With static recall that low, most risky skills would never be escalated, and at our test-set size running the LLM tier on every skill is cheap compared with the task runs in section 4 of evaluate.md. Worth revisiting only for skill breeding, where the same skills are rescanned many times.

### Cost and privacy

- `deep` sends the full skill contents to the configured LLM provider. Before the first call, show which provider receives what. This matters most for a skill that has not been generalized yet and still contains personal config.
- Measured cost of Cisco's LLM pass: about 4,400 input tokens for a small skill and 28,000 for a large one, plus a few hundred output tokens (details in [safety/cisco.md](safety/cisco.md)). **[OPEN]** A default cap per scan.
- Cisco's LLM analyzer would normally call a model with its own key. Hermes never gives plugins keys, so an adapter routes Cisco's requests through `ctx.llm` instead, the same access `judge.py` uses: Cisco and the judge run on the user's configured model, and Hermes handles auth. See [safety/cisco.md](safety/cisco.md), "How Cisco reaches the model".

## 5. Data model

Each backend implements one interface and returns normalized findings.

```python
class SafetyBackend(Protocol):
    name: str
    def available(self) -> bool: ...
    def scan(self, skill: Skill, tier: Tier) -> BackendResult: ...
```

```python
@dataclass
class Finding:
    dimension: Dimension  # "safety", or the dimension a finding is handed to (2.2)
    category: str         # one of the categories in section 2
    severity: Severity    # CRITICAL | HIGH | MEDIUM | LOW | INFO
    quote: str            # the offending text, verbatim
    location: str         # file and line
    message: str          # what is wrong, in plain words, for the report
    source: str           # "cisco" | "own-checks" | "llm-review"
    rule: str             # the backend's rule that fired

@dataclass
class BackendResult:
    backend: str
    status: BackendStatus # ok | not-installed | llm-unavailable | error
    tier: Tier            # the tier that actually ran
    findings: list[Finding]
    detail: str = ""      # what went wrong, or notes such as skipped files
```

- `Finding` has the same fields as a finding in the report (section 2.1 of evaluate.md).
- Backend versions are not stored per finding. They are the same for every finding in a run, so the report header records them once: `scanner_version` for Cisco and `checks_version` for our own checks. The LLM review is covered by `judge_model`.
- Severity uses Cisco's five levels as the common scale, since it is the only external scale we map from.
- Two findings with the same or overlapping category (2.1) at the same location are merged into one that lists both sources. This prevents the same problem being counted twice.

### Cisco adapter

The adapter is the one module that knows about Cisco's scanner (`cisco.py`). It runs the scanner on a skill directory and translates what comes back into our `Finding` and `BackendResult`. Nothing else in `evaluate` imports `skill_scanner`, so the scanner can be upgraded, swapped or left uninstalled without touching scoring or ranking.

It does five things:

1. **Checks availability.** The import is guarded. If the package is missing, `available()` returns false and `scan()` returns `status="not-installed"` with no findings.
2. **Picks analyzers from the tier.** `static` uses the scanner's default analyzers (`static_analyzer`, `bytecode`, `pipeline`, `correlation`), which need no key. `deep` builds them with `build_analyzers(policy, use_llm=True, ...)`, which adds `llm_analyzer`, pointed at `ctx.llm` through the adapter with a placeholder key.
3. **Runs the scan** through the SDK on one skill directory.
4. **Translates findings.** Field by field, as in the table below. The scanner's diagnostic notices go into `detail` (section 2.2), and findings with the scanner's sixth severity, `SAFE`, are skipped.
5. **Handles failure.** Verified against 2.2.1, the LLM layer fails in two ways:
   - **Setup**, such as a misconfigured model: `build_analyzers` raises `AnalyzerConfigurationError`, a `ValueError` and not a `SkillScannerError`. The adapter reruns as `static` and returns `llm-unavailable`.
   - **Run time**, such as the API being down: nothing is raised. The scan returns normally, with the failure in `analyzers_failed`. The adapter reads that list, keeps the static findings and returns `llm-unavailable`.

   Any other error returns `error` with the message; it never raises into the caller.

| Cisco finding | Our `Finding` | Note |
|---|---|---|
| — | `dimension` | `safety`, unless the category is handed to another dimension (section 2.2) |
| `category` | `category` | Unchanged (section 2.2) |
| `severity` | `severity` | Same five levels |
| `snippet` | `quote` | Can be empty, for findings about the skill as a whole |
| `file_path`, `line_number` | `location` | `line_number` can be missing |
| `title`, `description` | `message` | |
| `rule_id` | `rule` | |
| package version | — | Not stored per finding. Read once from `importlib.metadata` and recorded as `scanner_version` in the report header (`ReportMeta`); empty if the scanner did not run |

The implementation, its tests against recorded scanner output, and the verification runs are in [safety/cisco.md](safety/cisco.md). The scan result's `llm_usage` field is where the per-scan token cost can be read from (open question 4).

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
  __init__.py    # score(skill, config) -> DimensionScore; missing_dependencies(),
                 #   versions(), local_warnings()
  findings.py    # Finding, BackendResult, severity scale, merge
  cisco.py       # Cisco backend: SDK call, category and severity mapping
  checks.py      # own deterministic checks (3.2)
  review.py      # LLM review via judge.py, quote verification (3.3)
  local.py       # warnings about this machine's files; never scored (safety/local.md)
```

## 7. Validation

This is how we know the component works, and the numbers go in the final report.

Test set, kept small:

- **Benign controls**: real skills from public repos, expected to produce no findings.
- **Risky variants**: 3 or 4 real skills, each with one deliberate flaw added. One variant for each of our categories in 2.1, plus one scanner category: unconfirmed `rm -rf`, printed credential, `curl | sh`, hidden instruction, over-broad permissions.
- **Known-malicious examples**: any that ship with the scanner.

Measured per category, at each tier: detection rate and false-positive rate, for Cisco alone, own checks alone, and the combination.

## 8. Testing

- Own checks: fixture skills in `tests/safety/fixtures/skills/` (clean, one per category, warnings-only, confirmed), plus one test case per row of each table in [safety/checks.md](safety/checks.md).
- Cisco adapter: tests run against scanner output recorded from real Hermes skills (`tests/safety/fixtures/cisco/`), so CI does not need the package installed. They cover not installed, both LLM failure modes, notices and routing. One test runs the real scanner and is skipped when it is not installed.
- `score()`: fake backends, one test per row of the status table.
- Local warnings: a temporary folder as the home directory, so tests never look at the real machine.
- LLM review: fake judge returning canned output, including a finding whose quote is not in the skill, to assert it is dropped.
- Real scanner and LLM runs are excluded from CI, as in section 8 of evaluate.md.

## 9. Limitations

- No findings does not mean safe. The scanner we use says the same about itself.
- Static false positives on ordinary skills. With the worst-finding score in section 5, one such finding sets a skill's score. Known cases on the 59 local Hermes skills:
  - Cisco: a CRITICAL "Function constructor" finding on a Playwright `page.waitForFunction('...')` call in `p5js` (score 0.0), and MEDIUM `DATA_EXFIL_NETWORK_REQUESTS` on `arxiv`'s requests to arXiv, which is its purpose (score 0.6; open question 7). In the 8 Oct run, 11 skills had a HIGH or CRITICAL Cisco finding; the rest have not been triaged.
  - Own checks: 6 skills at HIGH after three noisy rules were narrowed, results in [safety/checks.md](safety/checks.md). Two in `github` are likely legitimate, such as a git credential helper that must print its token.
- The `deep` tier only partly reads large skills. Cisco's LLM analyzer has a context budget: on `p5js` it read about 20,000 characters of `SKILL.md` and skipped five reference files. `detail` lists what was skipped.
- Low static recall. Without `deep`, most malicious skills pass undetected.
- The LLM tier is not deterministic. Two runs can give different findings. Measured on `gpt-5.4-nano` over 25 scans: details varied (an extra LOW finding, a MEDIUM reported as LOW) but every skill's score was the same on every successful run.
- The LLM tier sometimes fails to produce a valid answer, because `ctx.llm` cannot make the provider enforce Cisco's schema. With the adapter's retry, 2 of 25 scans failed on `gpt-5.4-nano`, both on large skills; they fall back to static with status `partial`.
- The confirmation check in 3.2 is a heuristic. A confirmation step written far from the command, or phrased unusually, will be missed and reported as a finding.
- The warning check in 3.2 is also a heuristic. An unusually phrased sentence can be misread as a warning and skipped. Skipped matches are listed in `detail`, and the `deep` review re-checks them; at `static` they stay skipped.
- We read instructions, not behaviour. A skill that fetches its real instructions at run time looks clean.
- Cisco's published accuracy does not cover our careless categories; only our own validation set does, and it is small.

## 10. Open questions

1. Does the malicious layer need our own implementation? (1)
2. Do OpenClaw skills parse in Cisco's scanner without conversion? Hermes skills do. (3.1)
3. Score values. (5)
4. Default token cap per scan; the per-scan cost is now measured. (4)
5. Whether to add SkillSpector for `excess-scope`. (3.4)
6. Whether Cisco becomes a required dependency instead of an optional one. Depends on how the project declares dependencies, which is undecided (no dependency file yet; PyYAML is in the same position, and Cisco itself depends on PyYAML). (3.1)
7. Whether the `deep` review may ever make a result more lenient than the deterministic layers, by downgrading or clearing a finding. Example: Cisco's `DATA_EXFIL_NETWORK_REQUESTS` on `arxiv`'s requests to arXiv. If yes: which findings, by how much, keeping the original, and how to stop a skill talking the reviewer into it. Re-checking skipped warnings is not covered, because it can only add findings. See [safety/review.md](safety/review.md). (3.3)
