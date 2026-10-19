# cisco.py

Status: built and tested (`tests/safety/test_cisco.py`). Scanner behaviour below was verified by running Cisco skill-scanner 2.2.1 on 15 Oct 2026 against `~/.hermes/skills/creative/p5js`. The LLM failure case was tested against an unreachable local endpoint, so no skill content was sent anywhere.

## Purpose

The adapter between Cisco's scanner and our code. It runs the scanner on one skill and translates the result into a `BackendResult` of our `Finding`s. It is the only file that imports `skill_scanner`, so the scanner can be upgraded, replaced or left uninstalled without touching anything else.

Cisco covers the **malicious** side of safety: prompt injection, data exfiltration, obfuscation, malware signatures and the other categories in section 2.2 of the safety design.

## Interface

```python
class CiscoBackend:
    name = "cisco"
    def available(self) -> bool: ...
    def scan(self, skill: Skill, tier: SafetyTier) -> BackendResult: ...
    def version(self) -> str: ...          # "2.2.1", or "" if not installed
```

`version()` is extra to the protocol. `__init__.py` uses it to fill `scanner_version` in the report header.

## What the scanner actually does

Verified, and different in places from the sketch in the safety design.

**Default analyzers.** `SkillScanner()` with no arguments runs four static analyzers: `static_analyzer`, `bytecode`, `pipeline` and `correlation`. No key needed.

**Deep analyzers.** Built with:

```python
from skill_scanner.core.analyzer_factory import build_analyzers
from skill_scanner.core.scan_policy import ScanPolicy

analyzers = build_analyzers(
    ScanPolicy.default(), use_llm=True,
    llm_model=..., llm_api_key=..., llm_base_url=..., llm_provider=...,
)
SkillScanner(analyzers=analyzers).scan_skill(skill.path)
```

This returns the four static analyzers plus `llm_analyzer`.

**The LLM layer fails in two different ways, neither of which matches the sketch.**

| When | What happens |
|---|---|
| Setup: no key, unknown model | `build_analyzers` raises `AnalyzerConfigurationError`. It subclasses `ValueError`, not `SkillScannerError`. |
| Run time: API down, bad key, timeout | Nothing is raised. The scan returns normally with `analyzers_failed = [{"analyzer": "llm_analyzer", "error": "..."}]` and an extra INFO finding `LLM_ANALYSIS_FAILED`. |

The sketch's `except SkillScannerError` would catch neither: a setup failure would crash, and a run-time failure would be reported as a successful deep scan.

**The LLM analyzer reports on itself through findings.** On `p5js`, a deep scan returned 8 findings instead of 1. The extra 7 were INFO `policy_violation` notices from the LLM analyzer:

- `LLM_ANALYSIS_FAILED`
- `LLM_CONTEXT_BUDGET_EXCEEDED`, one per file it skipped or cut, e.g. `references/animation.md excluded from LLM analysis (10,430 chars)`

**The LLM only reads part of a large skill.** On `p5js` it read about 20,000 characters of `SKILL.md` and skipped five reference files completely.

**Result format.** `ScanResult.to_dict()` gives plain JSON. Each finding has `rule_id`, `category`, `severity`, `title`, `description`, `file_path` (relative to the skill, can be `None`), `line_number` (can be `None`), `snippet` (can be `None`), `remediation`, `analyzer` (`"static"` or `"llm"`) and `metadata`. There is no `from_dict`.

## Behaviour

### `available()`

True if `skill_scanner` can be imported, checked with `importlib.util.find_spec` so the heavy package is not loaded. This is what the install prompt uses: before evaluation starts, the tool calls it and, if false, the agent asks the user whether to install `cisco-ai-skill-scanner==2.2.1` (section 3.1 of the safety design).

### `scan(skill, tier)`

1. **Not installed:** return `not-installed`, no findings, `detail` with the install command.
2. **Static:** run `SkillScanner().scan_skill(skill.path)`.
3. **Deep:**
   1. Build analyzers with the judge's provider, model and key.
   2. If that raises `AnalyzerConfigurationError`, run the static scan instead and return `llm-unavailable`, with the error in `detail`.
   3. Otherwise scan. If `llm_analyzer` is in `analyzers_failed`, return `llm-unavailable` with the error in `detail`. The static analyzers still ran, so their findings are kept.
4. **Convert** `result.to_dict()` into our findings (below).
5. **Anything else raises:** return `error` with the message. Never raise into the caller.

Converting from `to_dict()` rather than from the scanner's objects means the real path and the tests use the same code: tests feed recorded JSON straight into the converter.

### Converting findings

| Cisco | Our `Finding` | Note |
|---|---|---|
| — | `dimension` | `safety`, except the routed categories below |
| `category` | `category` | Unchanged |
| `severity` | `severity` | Same five levels |
| `snippet` | `quote` | `""` when `None` |
| `file_path`, `line_number` | `location` | `path:line`, `path`, or `""` |
| `title`, `description` | `message` | `"title: description"` |
| — | `source` | `"cisco"` |
| `rule_id` | `rule` | Unchanged |

Findings with the scanner's sixth severity, `SAFE`, are skipped. In 2.2.1 the scanner only uses `SAFE` as the summary severity of a result with no findings, never on a finding; the check stops a future one from crashing the conversion, since our scale has no `SAFE`.

### Scanner notices are not findings

`LLM_ANALYSIS_FAILED` and `LLM_CONTEXT_BUDGET_EXCEEDED` describe the scan, not the skill. Passed through, they would cap any large skill at 0.9 (INFO under the worst-finding table) for being large, and penalize a skill when the API is down. The adapter drops them from findings and summarizes them in `detail`, for example "LLM analysis skipped 5 files over the context budget".

Decided. The list of notice rule IDs lives in one constant (`SCANNER_NOTICES`), so new ones are easy to add when the scanner is upgraded.

### Routed categories

Decided: Cisco's `hardcoded_secrets` and `policy_violation` are not about harm to the user, so they are kept with `dimension = config_cleanliness` (`ROUTED_CATEGORIES`). They stay in the report as evidence, `findings.py` already ignores them for the safety score, and Configuration Cleanliness can decide later whether to use them. Changing this is one constant.

## Configuration it needs

From `EvaluationConfig`: `safety_tier`, `judge_provider`, `judge_model`. The API key comes from the same place `judge.py` will read it, so Cisco's LLM and our judge always use the same model (section 4 of the safety design).

## Tests

Run against recorded output, so CI does not need the scanner installed.

- **Fixtures:** `tests/safety/fixtures/cisco/*.json`, made by running the real scanner once on a few skills and saving `to_dict()`:
  - a clean skill (no findings)
  - `p5js` static (one CRITICAL)
  - `p5js` deep with a failed LLM (static finding plus the notices)
- **Conversion:** fields map as in the table; missing line, missing snippet, whole-skill finding.
- **Notices:** dropped from findings, summarized in `detail`.
- **Routing:** `hardcoded_secrets` gets `config_cleanliness`.
- **Statuses:** not installed; setup failure falls back to static; run-time LLM failure gives `llm-unavailable` with static findings kept; unexpected exception gives `error`.

The scanner calls are wrapped in one small method so tests can replace it with a fake that returns a fixture or raises.

## Limitations to add to the safety design

- The deep tier only partly reads large skills. A `deep` result on a large skill is partly static-only; `detail` says which files were skipped.
- Static false positives remain, such as the CRITICAL `Function constructor` finding on Playwright's `page.waitForFunction('...')` in `p5js`.
