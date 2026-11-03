from metaskill.evaluate.models import Dimension, Finding, Severity
from metaskill.evaluate.safety.findings import merge, score_from_findings, worst_severity


def make(
    severity=Severity.HIGH,
    category="destructive-unconfirmed",
    location="SKILL.md:42",
    source="own-checks",
    rule="recursive-delete",
    quote="rm -rf build/",
    message="",
    dimension=Dimension.SAFETY,
):
    return Finding(dimension, category, severity, source, rule, quote, location, message)


# --- score ------------------------------------------------------------------


def test_no_findings_scores_one():
    assert score_from_findings([]) == 1.0


def test_score_is_set_by_worst_finding():
    findings = [make(Severity.LOW), make(Severity.CRITICAL), make(Severity.MEDIUM)]
    assert score_from_findings(findings) == 0.0


def test_more_findings_of_the_same_severity_do_not_lower_the_score():
    one = [make(Severity.MEDIUM)]
    many = [make(Severity.MEDIUM, location=f"SKILL.md:{n}") for n in range(5)]
    assert score_from_findings(one) == score_from_findings(many) == 0.6


def test_findings_for_another_dimension_do_not_count():
    secret = make(Severity.CRITICAL, category="hardcoded_secrets",
                  dimension=Dimension.CONFIG_CLEANLINESS)
    assert worst_severity([secret]) is None
    assert score_from_findings([secret]) == 1.0


# --- merge ------------------------------------------------------------------


def test_same_category_and_location_merge_and_list_both_sources():
    cisco = make(Severity.MEDIUM, source="cisco", rule="RM_RF")
    ours = make(Severity.HIGH)
    [merged] = merge([cisco, ours])
    assert merged.severity is Severity.HIGH
    assert merged.source == "own-checks, cisco"
    assert merged.rule == "recursive-delete, RM_RF"


def test_overlapping_categories_merge():
    cisco = make(category="supply_chain_attack", source="cisco", rule="UNPINNED")
    ours = make(category="untrusted-source", rule="curl-pipe-sh")
    assert len(merge([cisco, ours])) == 1


def test_different_locations_do_not_merge():
    assert len(merge([make(location="SKILL.md:1"), make(location="SKILL.md:2")])) == 2


def test_different_categories_do_not_merge():
    a = make(category="destructive-unconfirmed")
    b = make(category="credential-logging")
    assert len(merge([a, b])) == 2


def test_different_dimensions_do_not_merge():
    a = make(category="hardcoded_secrets")
    b = make(category="hardcoded_secrets", dimension=Dimension.CONFIG_CLEANLINESS)
    assert len(merge([a, b])) == 2


def test_leading_dot_slash_in_location_is_ignored():
    assert len(merge([make(location="./SKILL.md:42"), make(location="SKILL.md:42")])) == 1


def test_merged_finding_fills_an_empty_quote_or_message():
    cisco = make(Severity.CRITICAL, source="cisco", quote="", message="")
    ours = make(Severity.LOW, message="No confirmation step")
    [merged] = merge([cisco, ours])
    assert merged.quote == "rm -rf build/"
    assert merged.message == "No confirmation step"


def test_merging_three_backends_lists_each_source_once():
    findings = [make(source="cisco"), make(source="own-checks"),
                make(source="llm-review"), make(source="cisco")]
    [merged] = merge(findings)
    assert merged.source.split(", ") == ["cisco", "own-checks", "llm-review"]


def test_merge_orders_most_severe_first():
    findings = [make(Severity.LOW, location="a:1"), make(Severity.CRITICAL, location="b:1"),
                make(Severity.MEDIUM, location="c:1")]
    assert [f.severity for f in merge(findings)] == [
        Severity.CRITICAL, Severity.MEDIUM, Severity.LOW]
