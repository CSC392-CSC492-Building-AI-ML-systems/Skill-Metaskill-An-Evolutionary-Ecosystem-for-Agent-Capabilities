"""Clarity dimension (docs/evaluate-clarity.md). Entry point: score(skill, config).

The LLM judge rates the skill on a fixed rubric of anchored levels (Judge.rate). Every
rating must quote the skill; ratings whose quote is not found are dropped by the judge.
The score is the mean of the criteria, with a criterion the judge could not support
counted as 0.
"""

from __future__ import annotations

from typing import Any

from .judge import Criterion, Judge, JudgeError, JudgeUnavailable
from .models import Dimension, DimensionScore, EvaluationConfig, RubricRating, Skill, Status

# Bumped whenever a criterion, level or the instructions change, since ratings from
# different rubric versions do not compare.
RUBRIC_VERSION = "1"

# Five anchored levels per criterion, worst first; Judge.rate normalises them to
# 0, 0.25, 0.5, 0.75, 1 (scoring.md, Clarity). Weighted equally.
CRITERIA: tuple[Criterion, ...] = (
    Criterion(
        "purpose_and_trigger",
        "Does the skill state the problem it solves, when it should be used, and when it "
        "should not?",
        (
            "No purpose or trigger can be found, or they are misleading.",
            "The purpose has to be inferred from the steps; no trigger is given.",
            "The main purpose is identifiable, but when to use it, or its limits, are unclear.",
            "Purpose and trigger are stated; limits (when not to use it) are partial.",
            "The problem, when to use it, and its relevant limits are explicit.",
        ),
    ),
    Criterion(
        "conciseness",
        "Does the skill say what it needs to without padding, repetition, or long "
        "preambles? Code the agent must run is content, not padding.",
        (
            "Mostly padding: the instructions are buried in repetition, filler or tangents.",
            "Substantial padding or repetition; the reader skims past a lot to find the steps.",
            "Noticeable filler or repeated content in places, but the core is easy to find.",
            "Minor redundancy only.",
            "Every section earns its place; nothing could be cut without losing information.",
        ),
    ),
    Criterion(
        "accessible_language",
        "Can a reader with basic knowledge of the subject follow it without a glossary? "
        "Jargon, acronyms and technical or scientific names should be avoided, or "
        "explained in plain words where first used.",
        (
            "Dense, unexplained jargon throughout; only a specialist could follow it.",
            "Frequent unexplained jargon, acronyms or scientific names.",
            "Some unexplained terms a basic reader would have to look up.",
            "Nearly all terms are plain or explained; one or two slips.",
            "Plain words throughout; unavoidable terms are explained at first use.",
        ),
    ),
    Criterion(
        "readability",
        "Could a person reading the skill tell what it accomplishes, and follow it, "
        "without an AI summarising or explaining it?",
        (
            "A reader cannot tell what the skill does or what to do.",
            "The goal is guessable only after reading most of it; the steps are hard to follow.",
            "A knowledgeable reader can tell what it does and proceed with some guessing.",
            "The goal is clear and the steps can be followed with minor effort.",
            "The goal is clear at a glance, and a person could follow it as well as an agent, "
            "given its stated prerequisites.",
        ),
    ),
    Criterion(
        "structure_and_flow",
        "Are the sections organised, the steps in the order they must be done, and does "
        "the writing lead naturally from one step to the next?",
        (
            "Contradictory, or no order can be found.",
            "Steps are out of order or scattered across sections.",
            "The main sequence is understandable, but the reader must work out dependencies.",
            "Well ordered, with minor jumps.",
            "Logical sections, prerequisites before the steps that need them, smooth flow.",
        ),
    ),
    Criterion(
        "verifiability",
        "Can the reader check the required inputs, meaningful intermediate results, and "
        "that the task is complete?",
        (
            "Success cannot be checked from the instructions.",
            "Almost nothing can be checked.",
            "Some inputs or intermediate results can be checked.",
            "Completion can be checked; some intermediate gaps.",
            "Required inputs, meaningful intermediate checks and completion are observable.",
        ),
    ),
    Criterion(
        "failure_handling",
        "Does the skill say what to do when relevant things go wrong? A simple workflow "
        "whose stopping conditions are already clear can earn full credit without a "
        "separate section.",
        (
            "Relevant failures are ignored or mishandled.",
            "One generic note about failure.",
            "Common failures are covered, with meaningful gaps.",
            "Most relevant failures are covered, with recovery or stopping behaviour.",
            "Relevant failures and the recovery or stopping behaviour are clear.",
        ),
    ),
)

# Trusted: sent outside the material, so it never holds text from the skill (judge.md 2.2).
INSTRUCTIONS = (
    "You are rating how clear an agent skill is, both to a person reading it and to the "
    "agent that follows it. The skill's stated purpose is the `description` in the "
    "frontmatter of SKILL.md.\n"
    "- Judge the writing only, not whether the task is useful or safe.\n"
    "- Code blocks are instructions for the agent. Judge whether they are introduced and "
    "explained, not their length.\n"
    "- Longer is not clearer. Extra words are evidence against conciseness and never "
    "evidence for any criterion.\n"
    "- When a criterion is low because something is missing, quote the nearby text where "
    "it should have been and say in `reason` what is missing. Never quote text that is "
    "not in the skill.\n"
    "- Rate each criterion on its own: a skill can be concise and still full of jargon."
)


def score(skill: Skill, config: EvaluationConfig, llm: Any = None,
          judge: Judge | None = None) -> DimensionScore:
    """Clarity score for one skill.

    judge is the run's shared Judge (so ReportMeta.judge_model lists every model that
    answered); without one, a judge is built on llm, Hermes' ctx.llm. Raises
    JudgeUnavailable when neither is there, which the caller reports as `skipped`.
    """
    if judge is None:
        if llm is None:
            raise JudgeUnavailable("no judge model: ctx.llm is not available")
        judge = Judge(llm, provider=config.judge_provider, model=config.judge_model,
                      retries=config.judge_retries, max_chars=config.judge_max_chars)

    ratings = judge.rate(skill, list(CRITERIA), INSTRUCTIONS)
    return score_from_ratings(ratings)


def score_from_ratings(ratings: list[RubricRating]) -> DimensionScore:
    """The DimensionScore for the ratings the judge could support.

    A criterion with no rating counts as 0, so a skill cannot gain from the judge failing
    to quote a weakness, which it often does for something that is absent. Some missing
    is `partial`; all missing is an `error` with no score, since the judge's answer was
    unusable.
    """
    rated = {r.criterion: r for r in ratings}
    missing = [c.name for c in CRITERIA if c.name not in rated]
    if len(missing) == len(CRITERIA):
        raise JudgeError("the judge gave no rating with a verifiable quote")

    value = sum(r.rating for r in rated.values()) / len(CRITERIA)
    weakest = min(rated.values(), key=lambda r: r.rating)
    reason = f"{len(rated)}/{len(CRITERIA)} criteria rated; weakest: {weakest.criterion}"
    if not missing:
        return DimensionScore(Dimension.CLARITY, value, Status.OK, reason=reason,
                              rubric=ratings)
    return DimensionScore(
        Dimension.CLARITY, value, Status.PARTIAL, reason=reason,
        detail=f"no verifiable rating for {', '.join(missing)}, counted as 0",
        rubric=ratings)
