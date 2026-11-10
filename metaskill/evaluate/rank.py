"""Overall score, comparison and ordering of skills (docs/evaluate.md, section 5).

overall is a weighted mean of the six dimensions (weights in EvaluationConfig). A skill
with a dimension that scored None (skipped or failed) has no overall: it is not ranked,
but listed below the ranked skills with the scores it has and a note on what is missing.
Those scores are never averaged into a stand-in for overall.

Skill names, finding locations and status details come from the skill or its backends,
so they are cleaned before they are printed: a name must not be able to add a row to the
table or send escape codes to the terminal. table() also escapes the Markdown that could
make a link, image, HTML or code, in case its output is rendered as Markdown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping

from .judge import clean
from .models import (
    Dimension,
    DimensionScore,
    EvaluationConfig,
    Finding,
    Scores,
    Severity,
    SkillResult,
    Status,
    check_weights,
)

# Tie-breaks after overall and lift (evaluate.md 5.2): heaviest weight first.
TIE_BREAK = (
    Dimension.SAFETY,
    Dimension.QUALITY,
    Dimension.ROBUSTNESS,
    Dimension.CLARITY,
    Dimension.EFFICIENCY,
    Dimension.CONFIG_CLEANLINESS,
)

# Column order of the table (evaluate.md section 2).
COLUMNS = (
    Dimension.QUALITY,
    Dimension.ROBUSTNESS,
    Dimension.CLARITY,
    Dimension.CONFIG_CLEANLINESS,
    Dimension.EFFICIENCY,
    Dimension.SAFETY,
)
HEADINGS = {
    Dimension.QUALITY: "Quality",
    Dimension.ROBUSTNESS: "Robustness",
    Dimension.CLARITY: "Clarity",
    Dimension.CONFIG_CLEANLINESS: "Config Cleanliness",
    Dimension.EFFICIENCY: "Efficiency",
    Dimension.SAFETY: "Safety",
}

# Scores are compared rounded to this many decimals, so float noise in the mean does not
# decide a place.
_DECIMALS = 9
MAX_NAME_CHARS = 100  # of a skill name as printed
MAX_TEXT_CHARS = 200  # of a location or detail as printed
LOW_SAFETY = 0.5  # a recommended skill below this gets a warning
UNNAMED = "(unnamed)"  # a name with nothing left once cleaned
# Markdown that can fetch or run something when rendered: links and images ([]), HTML and
# autolinks (<>), code (`), and \ itself, so an escape cannot be undone. `_` and `*` only
# change formatting, and escaping them would show as `my\_skill` in a terminal.
_MARKDOWN = re.compile(r"([\\`\[\]<>])")


@dataclass
class RankedSkill:
    """One skill's place in a ranking."""

    result: SkillResult
    rank: int | None  # place from 1, shared on a full tie; None when not ranked
    overall: float | None  # None exactly when rank is None
    missing: list[Dimension] = field(default_factory=list)  # dimensions scored None
    notes: list[str] = field(default_factory=list)

    @property
    def ranked(self) -> bool:
        return self.rank is not None

    @property
    def name(self) -> str:
        """The skill's name, cleaned for printing."""
        return clean(self.result.name, MAX_NAME_CHARS) or UNNAMED


@dataclass
class Ranking:
    """Ranked skills first, then the ones that could not be ranked."""

    ranked: list[RankedSkill] = field(default_factory=list)
    unranked: list[RankedSkill] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # about the ranking as a whole
    recommendation: str = ""

    @property
    def entries(self) -> list[RankedSkill]:
        return self.ranked + self.unranked


def values(scores: Scores) -> dict[Dimension, float | None]:
    """The six dimension scores of `scores`, keyed by dimension."""
    return {d: getattr(scores, d.value) for d in Dimension}


def overall(dimension_values: Mapping[Dimension, float | None],
            weights: Mapping[Dimension, float]) -> float | None:
    """overall of a skill: the weighted mean of all six dimensions, or None if any of
    them has no value, whatever its weight. The rest are not averaged instead: a mean
    over fewer dimensions is a different assessment (evaluate.md 2.1)."""
    total = weight_sum = 0.0
    for dimension in Dimension:
        value = dimension_values.get(dimension)
        if value is None:
            return None
        weight = weights.get(dimension, 0.0)
        total += weight * value
        weight_sum += weight
    return min(1.0, max(0.0, total / weight_sum))


def make_scores(dimension_scores: Mapping[Dimension, DimensionScore],
                config: EvaluationConfig) -> Scores:
    """Scores for a SkillResult, from what each scoring module returned, weighted with
    config.weights. A dimension not in `dimension_scores` counts as not scored (None).
    """
    check_weights(config.weights)
    found = {d: (dimension_scores[d].value if d in dimension_scores else None)
             for d in Dimension}
    return Scores(overall=overall(found, config.weights),
                  **{d.value: v for d, v in found.items()})


def rank(results: list[SkillResult], config: EvaluationConfig) -> Ranking:
    """Order `results` (evaluate.md section 5), weighted with config.weights.

    overall is recomputed from the dimension scores, so it is always the one of these
    weights, the same as make_scores gives with the same config. Ranked skills come
    first, ordered by overall, then lift, then the dimensions in TIE_BREAK. Skills equal
    on all of these share a rank, and the next place is skipped (1, 1, 3). They are
    listed by name, which only keeps the output stable: it says nothing about them.
    Skills with a missing dimension follow, ordered by name: their scores do not compare,
    so that order says nothing about them. The same input always gives the same order.
    """
    weights = config.weights
    check_weights(weights)
    ranked: list[RankedSkill] = []
    unranked: list[RankedSkill] = []
    for result in results:
        found = values(result.scores)
        score = overall(found, weights)
        entry = RankedSkill(
            result=result,
            rank=None,
            overall=score,
            missing=[d for d in Dimension if found[d] is None],
        )
        (ranked if score is not None else unranked).append(entry)

    # The name is compared as text: a YAML name can be a number.
    ranked.sort(key=lambda e: (_standing(e), str(e.result.name)))
    unranked.sort(key=lambda e: (str(e.result.name), _standing(e)))
    for i, entry in enumerate(ranked):
        tied = i > 0 and _standing(ranked[i - 1]) == _standing(entry)
        entry.rank = ranked[i - 1].rank if tied else i + 1
    # Partial safety only makes a score incomparable when other skills here were
    # scanned in full (evaluate.md 5.2). When every skill is partial, say so once.
    statuses = {_safety_status(r) for r in results} & {Status.OK, Status.PARTIAL}
    mixed = len(statuses) == 2
    for entry in ranked + unranked:
        entry.notes = _notes(entry, mixed)
    notes = []
    if statuses == {Status.PARTIAL}:
        notes.append("Safety is partial for every skill with a safety score (e.g. a "
                     "scanner is not installed or a deep scan fell back to static): their "
                     "safety scores compare with each other, not with full scans.")
    return Ranking(ranked=ranked, unranked=unranked, notes=notes,
                   recommendation=_recommend(ranked, unranked, mixed))


def _standing(entry: RankedSkill) -> tuple:
    """What places a skill: skills with the same standing are tied. Higher is better for
    every score, so each is negated."""
    found = values(entry.result.scores)
    return (
        _desc(entry.overall),
        _desc(entry.result.lift),
        *(_desc(found[d]) for d in TIE_BREAK),
    )


def _desc(value: float | None) -> tuple[int, float]:
    """Sorts higher values first, and None after every value."""
    if value is None:
        return (1, 0.0)
    return (0, -round(value, _DECIMALS))


def _notes(entry: RankedSkill, mixed: bool) -> list[str]:
    result = entry.result
    notes: list[str] = []
    if not entry.ranked:
        notes.append(_unranked_note(entry))
    if mixed and _safety_status(result) is Status.PARTIAL:
        detail = clean(result.dimensions[Dimension.SAFETY].detail, MAX_TEXT_CHARS)
        detail = f" ({detail})" if detail else ""
        notes.append(f"Safety is partial{detail}: not comparable with the skills here "
                     "that were scanned in full.")
    critical = _critical(result)
    if critical:
        where = ", ".join(clean(f.location, MAX_TEXT_CHARS) for f in critical)
        notes.append(f"{len(critical)} CRITICAL safety finding(s) at {where}.")
    return notes


def _safety_status(result: SkillResult) -> Status | None:
    safety = result.dimensions.get(Dimension.SAFETY)
    return safety.status if safety is not None else None


def _critical(result: SkillResult) -> list[Finding]:
    return [f for f in result.findings
            if f.dimension is Dimension.SAFETY and f.severity is Severity.CRITICAL]


def _unranked_note(entry: RankedSkill) -> str:
    """Why a skill is not ranked: its missing dimensions, with status and detail."""
    why = ", ".join(_why(entry.result, d) for d in entry.missing)
    return f"Not ranked: no score for {why}."


def _why(result: SkillResult, dimension: Dimension) -> str:
    """A missing dimension, with whatever status and detail the result has for it."""
    found = result.dimensions.get(dimension)
    if found is None:
        return dimension.value
    detail = clean(found.detail, MAX_TEXT_CHARS)  # may leave nothing
    return f"{dimension.value} ({found.status}{f': {detail}' if detail else ''})"


def _recommend(ranked: list[RankedSkill], unranked: list[RankedSkill],
               mixed: bool) -> str:
    """Which skill to use, based on the ranking (evaluate.md 5.2)."""
    if ranked:
        top = [e for e in ranked if e.rank == 1]
        first, alone = top[0], len(top) == 1
        place = f"({first.overall:.2f}) of {len(ranked)} ranked skill(s)"
        if alone:
            text = f"Use {first.name}: highest overall {place}."
        else:  # picking one would decide on the name
            names = ", ".join(e.name for e in top)
            text = f"Tied for highest overall {place}: {names}. Nothing scored separates them."
        its = "its" if alone else "their"
        safety = values(first.result.scores)[Dimension.SAFETY]  # the same across a tie
        if any(_critical(e.result) or (mixed and _safety_status(e.result) is Status.PARTIAL)
               for e in top):
            text += f" Check {its} safety notes before using {'it' if alone else 'them'}."
        elif safety is not None and safety < LOW_SAFETY:
            text += (f" {its.capitalize()} safety score is low ({safety:.2f}); "
                     f"check {its} findings first.")
        return text + _lift_warning(first, alone)
    if unranked:  # no winner from an incomplete assessment (evaluate.md 2.1)
        return ("No complete overall comparison was available, so no skill is "
                "recommended. The notes say which dimensions each skill is missing.")
    return "No skills to rank."


def _lift_warning(entry: RankedSkill, alone: bool) -> str:
    """A skill no better than no skill adds no value (evaluate.md 4.1)."""
    if entry.result.lift > 0:
        return ""
    its, does = ("Its", "it does") if alone else ("Their", "they do")
    return (f" {its} lift is {entry.result.lift:+.2f}: on the tasks {does} no better than "
            "having no skill.")


def table(ranking: Ranking) -> str:
    """The terminal table of evaluate.md section 2, followed by the notes and the
    recommendation.

    Skills that are not ranked show `-` as their rank and overall, and for each dimension
    without a score. Names, notes and the recommendation are escaped (_escape), so the
    output is safe to render as Markdown.
    """
    head = ["Rank", "Skill", "Overall", *(HEADINGS[d] for d in COLUMNS)]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for entry in ranking.entries:
        found = values(entry.result.scores)
        place = str(entry.rank) if entry.ranked else "-"
        name = _escape(entry.name).replace("|", "\\|")
        row = [place, name, _cell(entry.overall), *(_cell(found[d]) for d in COLUMNS)]
        lines.append("| " + " | ".join(row) + " |")
    notes = [*ranking.notes,
             *(f"{e.name}: {note}" for e in ranking.entries for note in e.notes)]
    if notes:
        lines += ["", "Notes:", *(f"- {_escape(note)}" for note in notes)]
    if ranking.recommendation:
        lines += ["", f"Recommendation: {_escape(ranking.recommendation)}"]
    return "\n".join(lines)


def _escape(text: str) -> str:
    """text with the Markdown in _MARKDOWN escaped. The notes' own wording uses none of
    those characters, so only what came from the skill or a backend changes."""
    return _MARKDOWN.sub(r"\\\1", text)


def _cell(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"
