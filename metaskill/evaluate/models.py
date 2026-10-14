"""Evaluation data structures, shared by the scoring modules and rank.py.

Mirrors the report format in docs/evaluate.md, section 2.1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
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


def _check_unit(name: str, value: float) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be in [0, 1], got {value}")


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
        raise NotImplementedError

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReportMeta:
        raise NotImplementedError


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
        raise NotImplementedError

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestResult:
        raise NotImplementedError


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
        raise NotImplementedError

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RubricRating:
        raise NotImplementedError


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
        raise NotImplementedError

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Finding:
        raise NotImplementedError


@dataclass
class DimensionScore:
    """Return type of every scoring module's score(skill, ...)."""

    dimension: Dimension
    value: float
    reason: str = ""
    rubric: list[RubricRating] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def __post_init__(self) -> None:
        _check_unit("value", self.value)


@dataclass
class Scores:
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
        raise NotImplementedError

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Scores:
        raise NotImplementedError


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
        raise NotImplementedError

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillResult:
        raise NotImplementedError


@dataclass
class Report:
    """Contents of ranked-evaluation-[timestamp].json."""

    meta: ReportMeta
    skills: list[SkillResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Report:
        raise NotImplementedError
