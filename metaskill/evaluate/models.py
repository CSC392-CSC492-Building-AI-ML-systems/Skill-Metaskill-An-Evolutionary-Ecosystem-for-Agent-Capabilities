"""Evaluation data structures, shared by the scoring modules and rank.py.

Mirrors the report format in docs/evaluate.md, section 2.1.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
import re
from typing import Any

# Used for both the report filename and meta.timestamp, e.g. 2026-10-14-T15-30-02.
TIMESTAMP_FORMAT = "%Y-%m-%d-T%H-%M-%S"


class Dimension(StrEnum):
    QUALITY = "quality"
    ROBUSTNESS = "robustness"
    CLARITY = "clarity"
    CONFIG_CLEANLINESS = "config_cleanliness"
    EFFICIENCY = "efficiency"
    SAFETY = "safety"


class Variant(StrEnum):
    WITH_SKILL = "with_skill"
    BASELINE = "baseline"


class JudgeType(StrEnum):
    DETERMINISTIC = "deterministic"
    LLM = "llm"


class SafetyTier(StrEnum):
    STATIC = "static"
    DEEP = "deep"


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class CheckKind(StrEnum):
    """Deterministic checks a task can use (section 4.2)."""

    FILE_EXISTS = "file_exists"
    EXIT_CODE = "exit_code"
    OUTPUT_MATCHES = "output_matches"


def _check_unit(name: str, value: float) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be in [0, 1], got {value}")


# --- Inputs ---------------------------------------------------------------


@dataclass
class Skill:
    """A skill loaded from disk. Every scoring module takes one of these."""

    path: Path  # the skill directory
    name: str
    body: str  # contents of SKILL.md
    frontmatter: dict[str, Any] = field(default_factory=dict)
    # bundled files, relative to path
    files: list[Path] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> Skill:
        """Read SKILL.md, parse its frontmatter and list the bundled files."""
        path = Path(path)
        body = (path / "SKILL.md").read_text(encoding="utf-8-sig")
        frontmatter: dict[str, Any] = {}
        match = re.match(
            r"---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)", body, re.DOTALL)
        if match:
            try:
                import yaml
            except ImportError as e:
                raise ImportError(
                    "PyYAML is required to parse SKILL.md frontmatter. "
                    "Install it with `pip install pyyaml`."
                ) from e
            parsed = yaml.safe_load(match.group(1)) or {}
            if not isinstance(parsed, dict):
                raise ValueError(
                    f"{path / 'SKILL.md'}: frontmatter is not a mapping")
            frontmatter = parsed
        skip = {"__pycache__", "node_modules"}
        files = sorted(
            f.relative_to(path)
            for f in path.rglob("*")
            if f.is_file()
            and f.name != "SKILL.md"
            and not any(p.startswith(".") or p in skip for p in f.relative_to(path).parts)
        )
        return Skill(
            path=path,
            name=frontmatter.get("name", path.name),
            body=body,  # full file, so line numbers match SKILL.md
            frontmatter=frontmatter,
            files=files,
        )


@dataclass
class EvaluationConfig:
    """Settings for one evaluate or rank call."""

    runs_per_task: int = 3
    judge_provider: str = ""
    judge_model: str = ""
    safety_tier: SafetyTier = SafetyTier.STATIC
    # [OPEN] weighting of overall (evaluate.md, open question 2). Equal weights for now.
    weights: dict[Dimension, float] = field(
        default_factory=lambda: {d: 1 / len(Dimension) for d in Dimension})
    # [OPEN] truncation length for TestResult.output (evaluate.md, section 2.1).
    output_max_chars: int = 2000
    output_dir: Path = Path(".")

    def __post_init__(self) -> None:
        if self.runs_per_task < 1:
            raise ValueError(
                f"runs_per_task must be >= 1, got {self.runs_per_task}")
        for dimension, weight in self.weights.items():
            _check_unit(f"weights[{dimension}]", weight)

    def hash(self) -> str:
        """Hash of the settings that affect scores; becomes ReportMeta.config.

        output_dir is left out, since where reports are written does not change them.
        """
        settings = self.to_dict()
        del settings["output_dir"]
        encoded = json.dumps(settings, sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()[:16]

    def to_meta(self, timestamp: str, skills: list[str], scanner_version: str) -> ReportMeta:
        return ReportMeta(
            timestamp=timestamp,
            skills=skills,
            config=self.hash(),
            runs_per_task=self.runs_per_task,
            judge_model=self.judge_model,
            safety_tier=self.safety_tier,
            scanner_version=scanner_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "runs_per_task": self.runs_per_task,
            "judge_provider": self.judge_provider,
            "judge_model": self.judge_model,
            "safety_tier": self.safety_tier.value,
            "weights": {d.value: w for d, w in self.weights.items()},
            "output_max_chars": self.output_max_chars,
            "output_dir": str(self.output_dir),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvaluationConfig:
        config = EvaluationConfig(
            runs_per_task=data.get("runs_per_task", 3),
            judge_provider=data.get("judge_provider", ""),
            judge_model=data.get("judge_model", ""),
            safety_tier=SafetyTier(
                data.get("safety_tier", SafetyTier.STATIC.value)),
            output_max_chars=data.get("output_max_chars", 2000),
            output_dir=Path(data.get("output_dir", ".")),
        )
        if "weights" in data:
            config.weights = {
                Dimension(d): w for d, w in data["weights"].items()}
            config.__post_init__()
        return config


@dataclass
class Check:
    """How a task's outcome is judged (section 4.2).

    Deterministic checks set kind, target and expected. LLM checks set checklist.
    """

    judge: JudgeType
    kind: CheckKind | None = None
    target: str = ""  # file path for FILE_EXISTS; command for EXIT_CODE; unused otherwise
    expected: str = ""  # exit code or regex to match, as text
    checklist: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.judge is JudgeType.DETERMINISTIC and self.kind is None:
            raise ValueError("a deterministic check needs a kind")
        if self.judge is JudgeType.LLM and not self.checklist:
            raise ValueError("an LLM check needs a checklist")

    def to_dict(self) -> dict[str, Any]:
        return {
            "judge": self.judge.value,
            "kind": self.kind.value if self.kind else None,
            "target": self.target,
            "expected": self.expected,
            "checklist": self.checklist,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Check:
        kind = data.get("kind")
        return Check(
            judge=JudgeType(data["judge"]),
            kind=CheckKind(kind) if kind else None,
            target=data.get("target", ""),
            expected=data.get("expected", ""),
            checklist=data.get("checklist", []),
        )


@dataclass
class Task:
    """One human-written test task (section 4.3). Run with and without the skill."""

    task_id: str
    description: str
    prompt: str  # what the agent is asked to do
    checks: list[Check]
    # files copied in before each run
    fixtures: list[Path] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.checks:
            raise ValueError(f"task {self.task_id} has no checks")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "description": self.description,
            "prompt": self.prompt,
            "checks": [c.to_dict() for c in self.checks],
            "fixtures": [str(f) for f in self.fixtures],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        return Task(
            task_id=data["task_id"],
            description=data["description"],
            prompt=data["prompt"],
            checks=[Check.from_dict(c) for c in data["checks"]],
            fixtures=[Path(f) for f in data.get("fixtures", [])],
        )


# --- Report ---------------------------------------------------------------


@dataclass
class ReportMeta:
    """Settings a report was produced under. Ranking is only valid when these match (section 5)."""

    timestamp: str
    skills: list[str]
    config: str  # hash of the evaluation config
    runs_per_task: int = 3
    judge_model: str = ""
    safety_tier: SafetyTier = SafetyTier.STATIC
    scanner_version: str = ""

    def is_comparable(self, other: ReportMeta) -> bool:
        return (
            self.config == other.config
            and self.judge_model == other.judge_model
            and self.safety_tier == other.safety_tier
            and self.scanner_version == other.scanner_version
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "skills": self.skills,
            "config": self.config,
            "runs_per_task": self.runs_per_task,
            "judge_model": self.judge_model,
            "safety_tier": self.safety_tier.value,
            "scanner_version": self.scanner_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReportMeta:
        return ReportMeta(
            timestamp=data["timestamp"],
            skills=data["skills"],
            config=data["config"],
            runs_per_task=data.get("runs_per_task", 3),
            judge_model=data.get("judge_model", ""),
            safety_tier=SafetyTier(
                data.get("safety_tier", SafetyTier.STATIC.value)),
            scanner_version=data.get("scanner_version", ""),
        )


@dataclass
class Metrics:
    """Cost of a single task run, used for efficiency."""

    tokens: int
    seconds: float
    tool_calls: int


@dataclass
class TestResult:
    """One task, one variant, one run. Feeds Quality, Robustness and Efficiency."""

    __test__ = False  # stop pytest collecting this as a test class

    task_id: str
    description: str
    variant: Variant
    run: int  # 1..n
    judge: JudgeType
    passed: bool
    score: float
    reason: str
    metrics: Metrics
    output: str  # truncated, with a path to the full log
    evidence: str | None = None  # quoted text, LLM judging only

    def __post_init__(self) -> None:
        _check_unit("score", self.score)
        if self.run < 1:
            raise ValueError(f"run must be >= 1, got {self.run}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "description": self.description,
            "variant": self.variant,
            "run": self.run,
            "judge": self.judge,
            "passed": self.passed,
            "score": self.score,
            "reason": self.reason,
            "metrics": {
                "tokens": self.metrics.tokens,
                "seconds": self.metrics.seconds,
                "tool_calls": self.metrics.tool_calls,
            },
            "output": self.output,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestResult:
        return TestResult(
            task_id=data["task_id"],
            description=data["description"],
            variant=Variant(data["variant"]),
            run=data["run"],
            judge=JudgeType(data["judge"]),
            passed=data["passed"],
            score=data["score"],
            reason=data["reason"],
            metrics=Metrics(**data["metrics"]),
            output=data["output"],
            evidence=data.get("evidence", None),
        )


@dataclass
class RubricRating:
    """One Clarity rubric criterion (section 3.3)."""

    criterion: str
    rating: float  # anchored level, normalised to [0, 1]
    reason: str
    quote: str

    def __post_init__(self) -> None:
        _check_unit("rating", self.rating)

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "rating": self.rating,
            "reason": self.reason,
            "quote": self.quote,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RubricRating:
        return RubricRating(
            criterion=data["criterion"],
            rating=data["rating"],
            reason=data["reason"],
            quote=data["quote"],
        )


@dataclass
class Finding:
    """A problem found in the skill's files, for Safety or Configuration Cleanliness."""

    dimension: Dimension
    category: str
    severity: Severity
    source: str  # e.g. "cisco", "own-checks", "llm-review"
    rule: str
    quote: str
    location: str  # e.g. "SKILL.md:42"

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "category": self.category,
            "severity": self.severity,
            "source": self.source,
            "rule": self.rule,
            "quote": self.quote,
            "location": self.location,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Finding:
        return Finding(
            dimension=Dimension(data["dimension"]),
            category=data["category"],
            severity=Severity(data["severity"]),
            source=data["source"],
            rule=data["rule"],
            quote=data["quote"],
            location=data["location"],
        )


@dataclass
class DimensionScore:
    """Return type of every scoring module's score(skill, ...)."""

    dimension: Dimension
    value: float
    reason: str = ""
    rubric: list[RubricRating] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "value": self.value,
            "reason": self.reason,
            "rubric": [r.to_dict() for r in self.rubric],
            "findings": [f.to_dict() for f in self.findings],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DimensionScore:
        return DimensionScore(
            dimension=Dimension(data["dimension"]),
            value=data["value"],
            reason=data["reason"],
            rubric=[RubricRating.from_dict(r) for r in data["rubric"]],
            findings=[Finding.from_dict(f) for f in data["findings"]],
        )

    def __post_init__(self) -> None:
        _check_unit("value", self.value)


@dataclass
class Scores:
    """Return type of the overall scoring function."""

    overall: float
    quality: float
    robustness: float
    clarity: float
    config_cleanliness: float
    efficiency: float
    safety: float

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            _check_unit(name, value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall": self.overall,
            "quality": self.quality,
            "robustness": self.robustness,
            "clarity": self.clarity,
            "config_cleanliness": self.config_cleanliness,
            "efficiency": self.efficiency,
            "safety": self.safety,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Scores:
        return Scores(
            overall=data["overall"],
            quality=data["quality"],
            robustness=data["robustness"],
            clarity=data["clarity"],
            config_cleanliness=data["config_cleanliness"],
            efficiency=data["efficiency"],
            safety=data["safety"],
        )


@dataclass
class SkillResult:
    """Everything evaluated for one skill."""

    name: str
    scores: Scores
    lift: float  # pass_rate(with_skill) - pass_rate(baseline), in [-1, 1]
    tests: list[TestResult] = field(default_factory=list)
    rubric: list[RubricRating] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "scores": self.scores.to_dict(),
            "lift": self.lift,
            "tests": [t.to_dict() for t in self.tests],
            "rubric": [r.to_dict() for r in self.rubric],
            "findings": [f.to_dict() for f in self.findings],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillResult:
        return SkillResult(
            name=data["name"],
            scores=Scores.from_dict(data["scores"]),
            lift=data["lift"],
            tests=[TestResult.from_dict(t) for t in data["tests"]],
            rubric=[RubricRating.from_dict(r) for r in data["rubric"]],
            findings=[Finding.from_dict(f) for f in data["findings"]],
        )


@dataclass
class Report:
    """Contents of ranked-evaluation-[timestamp].json."""

    meta: ReportMeta
    skills: list[SkillResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "meta": self.meta.to_dict(),
            "skills": [s.to_dict() for s in self.skills],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Report:
        return Report(
            meta=ReportMeta.from_dict(data["meta"]),
            skills=[SkillResult.from_dict(s) for s in data["skills"]],
        )
