"""Judges: the LLM judge, and the deterministic judge for task checks.

The LLM judge is the one interface between the evaluate module and the judge model.
clarity.py, safety/review.py and the LLM part of config_cleanliness.py call the model
through this module, never directly (docs/evaluate.md, section 6).

Task outcomes are judged in one of two ways (evaluate.md 4.2): `DeterministicJudge`
(file exists, exit code, output matches) or `Judge` with a checklist. Both implement
`TaskJudge`, so runner.py picks one with `make_judge(JudgeType.LLM | DETERMINISTIC, ...)`
and calls `judge(task, check, run)` the same way on either.

The model is reached through Hermes' plugin LLM access (`ctx.llm`), so the judge runs on
the user's own provider and credentials and the plugin never handles an API key. Calls go
through the plugin's own auxiliary task, JUDGE_TASK: until the user pins a model for it
in `config.yaml` (`auxiliary.metaskill_judge`), it follows their active model.

Every rating, finding or checklist item must quote its supporting text. Quotes are
checked to be real substrings of what was judged; anything with a quote that is not
there is dropped (evaluate.md 3.3, evaluate-safety.md 3.3).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Protocol

from .models import (
    Check,
    CheckKind,
    Dimension,
    EvaluationConfig,
    Finding,
    JudgeType,
    RubricRating,
    Severity,
    Skill,
    Task,
)

# Auxiliary task registered in register(ctx); configured under auxiliary.metaskill_judge.
JUDGE_TASK = "metaskill_judge"


class JudgeUnavailable(Exception):
    """The judge cannot run, e.g. no provider configured. The dimension is `skipped`."""


class JudgeError(Exception):
    """The judge ran but its answer could not be used. The dimension is `error`."""


class PluginLlm(Protocol):
    """The part of Hermes' `ctx.llm` the judge uses (agent.plugin_llm.PluginLlm)."""

    def complete_structured(self, *, instructions: str, input: list[dict[str, Any]],
                            **kwargs: Any) -> Any: ...


# --- Evidence -----------------------------------------------------------------


def skill_sources(skill: Skill) -> dict[str, str]:
    """The text the judge sees, keyed by path relative to the skill: SKILL.md first,
    then every bundled file that decodes as UTF-8. Binary files are left out."""
    sources = {"SKILL.md": skill.body}
    for rel in skill.files:
        try:
            sources[rel.as_posix()] = (skill.path / rel).read_text(encoding="utf-8-sig")
        except (UnicodeDecodeError, OSError):
            continue
    return sources


def locate(sources: dict[str, str], quote: str) -> str | None:
    """`path:line` of the first exact occurrence of quote, or None if it is not there."""
    quote = _normalize(quote).strip()
    if not quote:
        return None
    for path, text in sources.items():
        text = _normalize(text)
        index = text.find(quote)
        if index != -1:
            return f"{path}:{text.count(chr(10), 0, index) + 1}"
    return None


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n")


def _render(sources: dict[str, str]) -> str:
    return "\n\n".join(
        f'<file path="{path}">\n{text}\n</file>' for path, text in sources.items())


SYSTEM_PROMPT = (
    "You are a strict, impartial evaluator of agent skills. Judge only what the "
    "material shows; length and polish are not quality. Support every answer with "
    "`quote`: text copied exactly, character for character, from the material you were "
    "given. Use one contiguous span, with no ellipses, paraphrase or added formatting. "
    "Answers whose quote cannot be found are discarded. Reply with JSON only."
)


# --- Judge --------------------------------------------------------------------


@dataclass(frozen=True)
class Criterion:
    """One rubric criterion with anchored levels, worst first (evaluate.md 3.3)."""

    name: str
    question: str
    levels: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.levels) < 2:
            raise ValueError(f"criterion {self.name} needs at least two levels")


@dataclass
class Verdict:
    """Outcome of judging one task run against one check (evaluate.md 4.2).

    Maps onto TestResult.passed, score, reason and evidence.
    """

    passed: bool
    score: float  # in [0, 1]; for a checklist, the fraction of items met
    reason: str
    evidence: str | None  # quoted output for LLM judging, otherwise None


@dataclass
class RunOutput:
    """What one agent run left behind, as the task judges see it."""

    text: str  # the agent's final output
    workdir: Path  # where the run happened; check targets resolve against it


class TaskJudge(Protocol):
    """Judges task runs. runner.py depends on this, not on a concrete judge."""

    judge_type: ClassVar[JudgeType]

    def judge(self, task: Task, check: Check, run: RunOutput) -> Verdict: ...


def _require(judge: TaskJudge, check: Check) -> None:
    if check.judge is not judge.judge_type:
        raise ValueError(
            f"{type(judge).__name__} cannot judge a {check.judge.value} check")


class Judge:
    """The judge model, behind Hermes' `ctx.llm`. Tests pass a fake `llm` instead.

    JudgeUnavailable is only known once a call is made, so callers catch it around
    rate, review, check and judge, not just at construction.
    """

    judge_type: ClassVar[JudgeType] = JudgeType.LLM

    def __init__(self, llm: PluginLlm, *, task: str = JUDGE_TASK,
                 provider: str = "", model: str = ""):
        self.llm = llm
        self.task = task
        # Explicit overrides from EvaluationConfig. Hermes only allows them once the
        # user sets plugins.entries.metaskill.llm.allow_provider/model_override.
        self.provider = provider
        self.model = model
        self.used: set[tuple[str, str]] = set()  # (provider, model) of every reply

    @classmethod
    def from_context(cls, ctx: Any, config: EvaluationConfig) -> Judge:
        """Build the judge from the plugin context passed to register(ctx)."""
        llm = getattr(ctx, "llm", None)
        if llm is None:
            raise JudgeUnavailable("this Hermes version has no plugin LLM access (ctx.llm)")
        return cls(llm, provider=config.judge_provider, model=config.judge_model)

    @property
    def judge_model(self) -> str:
        """What ran, for ReportMeta.judge_model. More than one entry means Hermes fell
        back to another model mid-run, so the scores are not all from one judge."""
        return ", ".join(sorted(f"{p}/{m}" for p, m in self.used))

    def ask(self, instructions: str, material: str, schema: dict[str, Any],
            purpose: str) -> dict[str, Any]:
        """One judge call, returning the parsed JSON object."""
        try:
            result = self.llm.complete_structured(
                instructions=instructions,
                input=[{"type": "text", "text": material}],
                json_schema=schema,
                schema_name=f"metaskill.{purpose}",
                system_prompt=SYSTEM_PROMPT,
                provider=self.provider or None,
                model=self.model or None,
                purpose=f"metaskill.evaluate.{purpose}",
                task=self.task,
            )
        except PermissionError as e:  # PluginLlmTrustError
            raise JudgeUnavailable(
                f"Hermes refused the judge's provider/model override: {e}. Either clear "
                "judge_provider/judge_model, or allow it under "
                "plugins.entries.metaskill.llm in config.yaml.") from e
        except ValueError as e:  # reply did not match the schema
            raise JudgeError(f"judge reply did not match the schema: {e}") from e
        except Exception as e:
            # No credentials or provider: Hermes raises AuxiliaryClientUnavailable. Matched
            # by name, since plugins should not import Hermes internals.
            if any(c.__name__ == "AuxiliaryClientUnavailable" for c in type(e).__mro__):
                raise JudgeUnavailable(str(e)) from e
            raise JudgeError(f"judge request failed: {e}") from e

        self.used.add((result.provider, result.model))
        if result.content_type != "json" or not isinstance(result.parsed, dict):
            raise JudgeError("judge reply is not a JSON object")
        return result.parsed

    def rate(self, skill: Skill, criteria: list[Criterion],
             instructions: str = "") -> list[RubricRating]:
        """Rate the skill on each criterion. A level is normalised to [0, 1].

        Criteria the judge skipped, or rated with a quote not in the skill, are left
        out; the caller decides how a missing criterion scores.
        """
        by_name = {c.name: c for c in criteria}
        rubric = "\n\n".join(
            f"## {c.name}\n{c.question}\nLevels, worst to best:\n"
            + "\n".join(f"{i}. {anchor}" for i, anchor in enumerate(c.levels, 1))
            for c in criteria)
        task = (
            f"{instructions}\n\n# Rubric\n\n{rubric}\n\n"
            "Rate the skill below on every criterion. `level` is the number of the "
            "level that fits best. Quotes come from the skill.").strip()
        schema = _object({"ratings": _array(_object({
            "criterion": {"type": "string", "enum": list(by_name)},
            "level": {"type": "integer"},
            "reason": {"type": "string"},
            "quote": {"type": "string"},
        }))})
        sources = skill_sources(skill)
        data = self.ask(task, _render(sources), schema, "rate")

        ratings: dict[str, RubricRating] = {}
        for item in data.get("ratings", []):
            criterion = by_name.get(item.get("criterion"))
            level = item.get("level")
            if criterion is None or criterion.name in ratings:
                continue
            if not isinstance(level, int) or not 1 <= level <= len(criterion.levels):
                continue
            if locate(sources, item.get("quote", "")) is None:
                continue
            ratings[criterion.name] = RubricRating(
                criterion=criterion.name,
                rating=(level - 1) / (len(criterion.levels) - 1),
                reason=item.get("reason", ""),
                quote=item["quote"].strip(),
            )
        return [ratings[c.name] for c in criteria if c.name in ratings]

    def review(self, skill: Skill, categories: dict[str, str], *,
               dimension: Dimension, source: str = "llm-review",
               instructions: str = "") -> list[Finding]:
        """Look for problems in the skill. `categories` maps category name to what it
        means. Findings with an unknown category or severity, or a quote not in the
        skill, are dropped."""
        listing = "\n".join(f"- `{name}`: {meaning}" for name, meaning in categories.items())
        task = (
            f"{instructions}\n\n# Categories\n\n{listing}\n\n"
            "Report each problem in the skill below as one finding. Report nothing for "
            "a category with no problem; an empty list is a valid answer. Quotes come "
            "from the skill.").strip()
        schema = _object({"findings": _array(_object({
            "category": {"type": "string", "enum": list(categories)},
            "severity": {"type": "string", "enum": [s.value for s in Severity]},
            "message": {"type": "string"},
            "quote": {"type": "string"},
        }))})
        sources = skill_sources(skill)
        data = self.ask(task, _render(sources), schema, "review")

        findings = []
        for item in data.get("findings", []):
            category = item.get("category")
            if category not in categories or item.get("severity") not in Severity:
                continue
            location = locate(sources, item.get("quote", ""))
            if location is None:
                continue
            findings.append(Finding(
                dimension=dimension,
                category=category,
                severity=Severity(item["severity"]),
                source=source,
                rule=category,
                quote=item["quote"].strip(),
                location=location,
                message=item.get("message", ""),
            ))
        return findings

    def judge(self, task: Task, check: Check, run: RunOutput) -> Verdict:
        """TaskJudge entry point: judge the run's output against the check's checklist."""
        _require(self, check)
        return self.check(task, run.text, check.checklist)

    def check(self, task: Task, output: str, checklist: list[str]) -> Verdict:
        """Judge an agent's output for a task against a checklist (evaluate.md 4.2).

        An item counts as met only if the judge says so and its quote is in the
        output. The task passes when every item is met.
        """
        if not checklist:
            raise ValueError("an LLM check needs a checklist")
        items = "\n".join(f"{i}. {item}" for i, item in enumerate(checklist, 1))
        instructions = (
            f"An agent was given this task:\n\n{task.prompt}\n\n# Checklist\n\n{items}\n\n"
            "For every checklist item, say whether the agent output below meets it. "
            "`item` is the item's number. Quotes come from the agent output.")
        schema = _object({"items": _array(_object({
            "item": {"type": "integer"},
            "met": {"type": "boolean"},
            "reason": {"type": "string"},
            "quote": {"type": "string"},
        }))})
        data = self.ask(instructions, f"<output>\n{output}\n</output>", schema, "check")

        sources = {"output": output}
        met: dict[int, str] = {}
        reasons: dict[int, str] = {}
        for entry in data.get("items", []):
            number = entry.get("item")
            if not isinstance(number, int) or not 1 <= number <= len(checklist):
                continue
            if number in reasons:
                continue
            reasons[number] = entry.get("reason", "")
            if entry.get("met") is True and locate(sources, entry.get("quote", "")):
                met[number] = entry["quote"].strip()

        unmet = [n for n in range(1, len(checklist) + 1) if n not in met]
        if unmet:
            reason = "; ".join(
                f"unmet: {checklist[n - 1]}" + (f" ({reasons[n]})" if reasons.get(n) else "")
                for n in unmet)
        else:
            reason = "all checklist items met"
        return Verdict(
            passed=not unmet,
            score=len(met) / len(checklist),
            reason=reason,
            evidence="\n".join(met[n] for n in sorted(met)) or None,
        )


class DeterministicJudge:
    """Judges a run with a machine-checkable condition (evaluate.md 4.2). No model call.

    A check that cannot be evaluated as written (bad regex, non-integer exit code, a
    path outside the run folder) raises JudgeError: the task is broken, not the skill.
    """

    judge_type: ClassVar[JudgeType] = JudgeType.DETERMINISTIC

    def __init__(self, *, timeout: float = 60.0):
        self.timeout = timeout  # seconds an EXIT_CODE command may run

    def judge(self, task: Task, check: Check, run: RunOutput) -> Verdict:
        _require(self, check)
        if check.kind is CheckKind.FILE_EXISTS:
            return self._file_exists(check, run)
        if check.kind is CheckKind.EXIT_CODE:
            return self._exit_code(check, run)
        if check.kind is CheckKind.OUTPUT_MATCHES:
            return self._output_matches(check, run)
        raise JudgeError(f"task {task.task_id}: unknown check kind {check.kind}")

    def _file_exists(self, check: Check, run: RunOutput) -> Verdict:
        if not check.target:
            raise JudgeError("a file_exists check needs a target path")
        root = run.workdir.resolve()
        path = (root / check.target).resolve()
        if not path.is_relative_to(root):
            raise JudgeError(f"file_exists target {check.target} is outside the run folder")
        found = path.exists()
        return _binary(found, f"{check.target} {'exists' if found else 'does not exist'}")

    def _exit_code(self, check: Check, run: RunOutput) -> Verdict:
        if not check.target:
            raise JudgeError("an exit_code check needs a target command")
        try:
            expected = int(check.expected or "0")
        except ValueError:
            raise JudgeError(
                f"exit_code expected must be an integer, got {check.expected!r}") from None
        try:
            # Task commands are human-written (evaluate.md 4.3), so a shell is fine here.
            result = subprocess.run(
                check.target, shell=True, cwd=run.workdir, capture_output=True,
                text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return _binary(False, f"`{check.target}` timed out after {self.timeout:g}s")
        reason = f"`{check.target}` exited {result.returncode}, expected {expected}"
        if result.returncode != expected and result.stderr.strip():
            reason += f": {result.stderr.strip()[-200:]}"
        return _binary(result.returncode == expected, reason)

    def _output_matches(self, check: Check, run: RunOutput) -> Verdict:
        try:
            pattern = re.compile(check.expected, re.MULTILINE)
        except re.error as e:
            raise JudgeError(f"output_matches regex {check.expected!r} is invalid: {e}") from e
        found = pattern.search(run.text) is not None
        return _binary(
            found, f"output {'matches' if found else 'does not match'} /{check.expected}/")


def _binary(passed: bool, reason: str) -> Verdict:
    return Verdict(passed=passed, score=1.0 if passed else 0.0, reason=reason, evidence=None)


def make_judge(judge_type: JudgeType | str, *, ctx: Any = None,
               config: EvaluationConfig | None = None, timeout: float = 60.0) -> TaskJudge:
    """The task judge of the given type, e.g. `make_judge(check.judge, ctx=ctx, config=c)`.

    The LLM judge needs the plugin context; without it this raises JudgeUnavailable.
    """
    judge_type = JudgeType(judge_type)
    if judge_type is JudgeType.DETERMINISTIC:
        return DeterministicJudge(timeout=timeout)
    if ctx is None:
        raise JudgeUnavailable("the LLM judge needs the plugin context (ctx.llm)")
    return Judge.from_context(ctx, config or EvaluationConfig())


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _array(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}
