"""Overall score, comparison and ordering of skills (docs/evaluate.md, section 5).

overall is a weighted mean of the six dimensions (weights in EvaluationConfig). A skill
with a dimension that scored None (skipped or failed) has no overall: it is not ranked,
but listed below the ranked skills with a note on how it did on what was scored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .models import (
    DEFAULT_WEIGHTS,
    Dimension,
    DimensionScore,
    Finding,
    Scores,
    Severity,
    SkillResult,
    Status,
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

# Scores closer than this count as equal, so float noise in the mean is not a ranking.
_EPSILON = 1e-9


@dataclass
class RankedSkill:
    """One skill's place in a ranking."""

    result: SkillResult
    rank: int | None  # 1..k for ranked skills; None when not ranked
    overall: float | None  # None exactly when rank is None
    # Weighted mean over the dimensions that were scored, for skills that are not
    # ranked; None if nothing was scored. Equal to overall for ranked skills.
    partial_overall: float | None
    missing: list[Dimension] = field(default_factory=list)  # dimensions scored None
    notes: list[str] = field(default_factory=list)

    @property
    def ranked(self) -> bool:
        return self.rank is not None


@dataclass
class Ranking:
    """Ranked skills first, then the ones that could not be ranked."""

    ranked: list[RankedSkill] = field(default_factory=list)
    unranked: list[RankedSkill] = field(default_factory=list)
    recommendation: str = ""

    @property
    def entries(self) -> list[RankedSkill]:
        return self.ranked + self.unranked


def values(scores: Scores) -> dict[Dimension, float | None]:
    """The six dimension scores of `scores`, keyed by dimension."""
    return {d: getattr(scores, d.value) for d in Dimension}


def weighted_mean(dimension_values: Mapping[Dimension, float | None],
                  weights: Mapping[Dimension, float] = DEFAULT_WEIGHTS) -> float | None:
    """Weighted mean of the dimensions that have a value; None if none of weight > 0 has.

    Dimensions without a value are left out of both the sum and the weight total, so
    this is the partial overall of a skill that is not ranked.
    """
    total = weight_sum = 0.0
    for dimension, value in dimension_values.items():
        weight = weights.get(dimension, 0.0)
        if value is None or weight == 0:
            continue
        total += weight * value
        weight_sum += weight
    if weight_sum == 0:
        return None
    return min(1.0, max(0.0, total / weight_sum))


def overall(dimension_values: Mapping[Dimension, float | None],
            weights: Mapping[Dimension, float] = DEFAULT_WEIGHTS) -> float | None:
    """overall of a skill: the weighted mean, or None if any dimension has no value.

    A dimension of weight 0 does not count, so its missing value does not either.
    """
    for dimension in Dimension:
        if weights.get(dimension, 0.0) > 0 and dimension_values.get(dimension) is None:
            return None
    return weighted_mean(dimension_values, weights)


def make_scores(dimension_scores: Mapping[Dimension, DimensionScore],
                weights: Mapping[Dimension, float] = DEFAULT_WEIGHTS) -> Scores:
    """Scores for a SkillResult, from what each scoring module returned.

    A dimension not in `dimension_scores` counts as not scored (None).
    """
    found = {d: (dimension_scores[d].value if d in dimension_scores else None)
             for d in Dimension}
    return Scores(overall=overall(found, weights),
                  **{d.value: v for d, v in found.items()})


def rank(results: list[SkillResult],
         weights: Mapping[Dimension, float] = DEFAULT_WEIGHTS) -> Ranking:
    """Order `results` (evaluate.md section 5).

    overall is recomputed from the dimension scores and `weights`, so a ranking never
    depends on an overall computed elsewhere with other weights. Ranked skills come
    first, ordered by overall, then lift, then the dimensions in TIE_BREAK, then name.
    Skills with a missing dimension follow, ordered the same way by their partial
    overall. The same input always gives the same order.
    """
    ranked: list[RankedSkill] = []
    unranked: list[RankedSkill] = []
    for result in results:
        found = values(result.scores)
        score = overall(found, weights)
        entry = RankedSkill(
            result=result,
            rank=None,
            overall=score,
            partial_overall=weighted_mean(found, weights),
            missing=[d for d in Dimension
                     if found[d] is None and weights.get(d, 0.0) > 0],
        )
        (ranked if score is not None else unranked).append(entry)

    ranked.sort(key=lambda e: _order(e, e.overall))
    unranked.sort(key=lambda e: _order(e, e.partial_overall))
    for i, entry in enumerate(ranked, start=1):
        entry.rank = i
    for entry in ranked + unranked:
        entry.notes = _notes(entry, ranked)
    return Ranking(ranked=ranked, unranked=unranked,
                   recommendation=_recommend(ranked, unranked))


def _order(entry: RankedSkill, score: float | None) -> tuple:
    """Sort key: higher is better for every score, so each is negated."""
    found = values(entry.result.scores)
    return (
        _desc(score),
        _desc(entry.result.lift),
        *(_desc(found[d]) for d in TIE_BREAK),
        entry.result.name,
    )


def _desc(value: float | None) -> tuple[int, float]:
    """Sorts higher values first, and None after every value."""
    if value is None:
        return (1, 0.0)
    return (0, -round(value, 9))


def _notes(entry: RankedSkill, ranked: list[RankedSkill]) -> list[str]:
    result = entry.result
    notes: list[str] = []
    if not entry.ranked:
        notes.append(_unranked_note(entry, ranked))
    if _safety_partial(result):
        detail = result.dimensions[Dimension.SAFETY].detail
        detail = f" ({detail})" if detail else ""
        notes.append(
            f"Safety is partial{detail}: not comparable with skills scanned in full.")
    critical = _critical(result)
    if critical:
        where = ", ".join(f.location for f in critical)
        notes.append(f"{len(critical)} CRITICAL safety finding(s) at {where}.")
    return notes


def _safety_partial(result: SkillResult) -> bool:
    """Safety ran with less than was asked for, e.g. deep fell back to static (5.2)."""
    safety = result.dimensions.get(Dimension.SAFETY)
    return safety is not None and safety.status is Status.PARTIAL


def _critical(result: SkillResult) -> list[Finding]:
    return [f for f in result.findings
            if f.dimension is Dimension.SAFETY and f.severity is Severity.CRITICAL]


def _unranked_note(entry: RankedSkill, ranked: list[RankedSkill]) -> str:
    """How a skill that is not ranked did on the dimensions that were scored."""
    statuses = entry.result.dimensions
    why = ", ".join(
        f"{d.value} ({statuses[d].status}: {statuses[d].detail})"
        if d in statuses and statuses[d].detail else d.value
        for d in entry.missing)
    note = f"Not ranked: no score for {why}."
    if entry.partial_overall is None:
        return note + " No dimension was scored."
    found = values(entry.result.scores)
    scored = {d: v for d, v in found.items() if v is not None}
    best = max(scored, key=lambda d: (scored[d], -TIE_BREAK.index(d)))
    worst = min(scored, key=lambda d: (scored[d], TIE_BREAK.index(d)))
    note += (f" On the {len(scored)} scored dimension(s) it averages "
             f"{entry.partial_overall:.2f}")
    # Where it would stand among the ranked skills on that average. Only indicative:
    # the ranked skills' overall also counts the dimensions this one is missing.
    above = sum(1 for e in ranked
                if e.overall is not None and e.overall > entry.partial_overall + _EPSILON)
    if ranked:
        note += f", which would place it {_ordinal(above + 1)} of {len(ranked) + 1}"
    note += f"; best: {best.value} ({scored[best]:.2f}), worst: {worst.value} ({scored[worst]:.2f})."
    return note


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _recommend(ranked: list[RankedSkill], unranked: list[RankedSkill]) -> str:
    """Which skill to use, based on the ranking (evaluate.md 5.2)."""
    if ranked:
        top = ranked[0]
        text = (f"Use {top.result.name}: highest overall ({top.overall:.2f}) "
                f"of {len(ranked)} ranked skill(s).")
        safety = values(top.result.scores)[Dimension.SAFETY]
        if _critical(top.result) or _safety_partial(top.result):
            text += " Check its safety notes before using it."
        elif safety is not None and safety < 0.5:
            text += f" Its safety score is low ({safety:.2f}); check its findings first."
        return text
    candidates = [e for e in unranked if e.partial_overall is not None]
    if candidates:
        best = candidates[0]
        return (f"No skill could be fully ranked. On the dimensions that were scored, "
                f"{best.result.name} did best ({best.partial_overall:.2f}); "
                f"rerun with the missing dimensions before relying on it.")
    if unranked:
        return "No skill could be ranked: no dimension was scored."
    return "No skills to rank."


def table(ranking: Ranking) -> str:
    """The terminal table of evaluate.md section 2, followed by each skill's notes.

    Skills that are not ranked show `-` as their rank, their partial overall in
    parentheses, and `-` for each dimension without a score.
    """
    head = ["Rank", "Skill", "Overall", *(HEADINGS[d] for d in COLUMNS)]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for entry in ranking.entries:
        found = values(entry.result.scores)
        if entry.ranked:
            place, score = str(entry.rank), _cell(entry.overall)
        else:
            place = "-"
            score = "-" if entry.partial_overall is None else f"({entry.partial_overall:.2f})"
        row = [place, entry.result.name, score, *(_cell(found[d]) for d in COLUMNS)]
        lines.append("| " + " | ".join(row) + " |")
    notes = [f"- {e.result.name}: {note}" for e in ranking.entries for note in e.notes]
    if notes:
        lines += ["", "Notes:", *notes]
    if ranking.recommendation:
        lines += ["", f"Recommendation: {ranking.recommendation}"]
    return "\n".join(lines)


def _cell(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"
