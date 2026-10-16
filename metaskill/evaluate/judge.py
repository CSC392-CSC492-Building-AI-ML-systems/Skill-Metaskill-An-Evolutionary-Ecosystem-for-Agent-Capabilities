"""General LLM judge: the one interface between the evaluate module and the judge model.

clarity.py, safety/review.py and the LLM part of config_cleanliness.py call the model
through this module, never directly (docs/evaluate.md, section 6). The one exception
is the Cisco scanner's LLM analyzer, which is configured from Judge.provider and
Judge.model (docs/evaluate-safety.md, section 4).

Every rating, finding or checklist item must quote its supporting text. Quotes are
checked to be real substrings of what was judged; anything with a quote that is not
there is dropped (evaluate.md 3.3, evaluate-safety.md 3.3).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol

from .models import (
    Dimension,
    EvaluationConfig,
    Finding,
    RubricRating,
    Severity,
    Skill,
    Task,
)

DEFAULT_PROVIDER = "anthropic"
DEFAULT_MODEL = "claude-opus-5-5"


class JudgeUnavailable(Exception):
    """The judge cannot run, e.g. no API key or SDK. The dimension is `skipped`."""


class JudgeError(Exception):
    """The judge ran but its answer could not be used. The dimension is `error`."""


# --- Backends ---------------------------------------------------------------


class Backend(Protocol):
    """Sends one request to a model and returns its reply as JSON text."""

    def complete(self, system: str, prompt: str, schema: dict[str, Any]) -> str: ...


class AnthropicBackend:
    """Claude through the Anthropic SDK, with the reply constrained to `schema`."""

    def __init__(self, model: str, effort: str = "high", max_tokens: int = 16000):
        try:
            import anthropic
        except ImportError as e:
            raise JudgeUnavailable(
                "the anthropic package is not installed (`pip install anthropic`)") from e
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise JudgeUnavailable("no ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN set")
        self._anthropic = anthropic
        self._client = anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens

    def complete(self, system: str, prompt: str, schema: dict[str, Any]) -> str:
        try:
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
                # On a safety decline, rerun on a fallback model instead of failing.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except self._anthropic.APIError as e:
            raise JudgeError(f"judge request failed: {e}") from e
        if response.stop_reason == "refusal":
            raise JudgeError("judge model refused the request")
        if response.stop_reason == "max_tokens":
            raise JudgeError("judge reply was cut off at max_tokens")
        return "".join(b.text for b in response.content if b.type == "text")


def make_backend(provider: str, model: str) -> Backend:
    """Build the backend for a provider name. Raises JudgeUnavailable if it cannot run."""
    if provider == "anthropic":
        return AnthropicBackend(model)
    raise JudgeUnavailable(f"unsupported judge provider: {provider!r}")


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


QUOTE_RULE = (
    "Support every answer with `quote`: text copied exactly, character for character, "
    "from the material you were given. Use one contiguous span, with no ellipses, "
    "paraphrase or added formatting. Answers whose quote cannot be found are discarded."
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
class ChecklistVerdict:
    """Outcome of judging one task output against its checklist (evaluate.md 4.2).

    Maps onto TestResult.passed, score, reason and evidence.
    """

    passed: bool
    score: float  # fraction of checklist items met
    reason: str
    evidence: str | None


class Judge:
    """The judge model. Scoring modules take one of these, so tests can pass a fake
    backend instead of calling a model."""

    def __init__(self, backend: Backend, provider: str, model: str):
        self.backend = backend
        self.provider = provider
        self.model = model  # recorded as ReportMeta.judge_model

    @classmethod
    def from_config(cls, config: EvaluationConfig) -> Judge:
        """Raises JudgeUnavailable when the configured provider cannot run."""
        provider = config.judge_provider or DEFAULT_PROVIDER
        model = config.judge_model or DEFAULT_MODEL
        return cls(make_backend(provider, model), provider, model)

    def ask(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        """One judge call, returning the parsed JSON object."""
        reply = self.backend.complete(system, prompt, schema)
        try:
            data = json.loads(reply)
        except json.JSONDecodeError as e:
            raise JudgeError(f"judge reply is not valid JSON: {e}") from e
        if not isinstance(data, dict):
            raise JudgeError("judge reply is not a JSON object")
        return data

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
        prompt = (
            f"{instructions}\n\n# Rubric\n\n{rubric}\n\n"
            f"# Skill\n\n{_render(skill_sources(skill))}\n\n"
            "Rate the skill on every criterion. `level` is the number of the level "
            "that fits best.").strip()
        schema = _object({"ratings": _array(_object({
            "criterion": {"type": "string", "enum": list(by_name)},
            "level": {"type": "integer"},
            "reason": {"type": "string"},
            "quote": {"type": "string"},
        }))})
        data = self.ask(_system(), prompt, schema)

        sources = skill_sources(skill)
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
        prompt = (
            f"{instructions}\n\n# Categories\n\n{listing}\n\n"
            f"# Skill\n\n{_render(skill_sources(skill))}\n\n"
            "Report each problem you find as one finding. Report nothing for a "
            "category with no problem; an empty list is a valid answer.").strip()
        schema = _object({"findings": _array(_object({
            "category": {"type": "string", "enum": list(categories)},
            "severity": {"type": "string", "enum": [s.value for s in Severity]},
            "message": {"type": "string"},
            "quote": {"type": "string"},
        }))})
        data = self.ask(_system(), prompt, schema)

        sources = skill_sources(skill)
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

    def check(self, task: Task, output: str, checklist: list[str]) -> ChecklistVerdict:
        """Judge an agent's output for a task against a checklist (evaluate.md 4.2).

        An item counts as met only if the judge says so and its quote is in the
        output. The task passes when every item is met.
        """
        if not checklist:
            raise ValueError("an LLM check needs a checklist")
        items = "\n".join(f"{i}. {item}" for i, item in enumerate(checklist, 1))
        prompt = (
            f"# Task\n\n{task.prompt}\n\n# Checklist\n\n{items}\n\n"
            f"# Agent output\n\n<output>\n{output}\n</output>\n\n"
            "For every checklist item, say whether the agent output meets it. "
            "`item` is the item's number. Quotes come from the agent output.")
        schema = _object({"items": _array(_object({
            "item": {"type": "integer"},
            "met": {"type": "boolean"},
            "reason": {"type": "string"},
            "quote": {"type": "string"},
        }))})
        data = self.ask(_system(), prompt, schema)

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
        return ChecklistVerdict(
            passed=not unmet,
            score=len(met) / len(checklist),
            reason=reason,
            evidence="\n".join(met[n] for n in sorted(met)) or None,
        )


def _system() -> str:
    return (
        "You are a strict, impartial evaluator of agent skills. Judge only what the "
        "material shows; length and polish are not quality. " + QUOTE_RULE)


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _array(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}
