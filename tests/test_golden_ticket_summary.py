"""Offline gold integrity and adversarial verifier checks, not live evaluation."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from metaskill.evaluate.golden.ticket_summary import assess_output, expected_report

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "metaskill/evaluate/golden/synthetic/ticket-summary"
MANIFEST = json.loads((PILOT / "cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", MANIFEST["cases"], ids=lambda c: c["case_id"])
def test_hand_authored_answers_agree_with_contract_oracle(case):
    tickets = json.loads((PILOT / case["input"]).read_text(encoding="utf-8"))
    gold = json.loads((PILOT / case["expected"]).read_text(encoding="utf-8"))
    profile = json.loads((PILOT / case["profile"]).read_text(encoding="utf-8"))
    assert expected_report(tickets, profile["organization"]) == gold
    assert assess_output(json.dumps(gold), gold)[0]


def test_verifier_accepts_object_key_reordering_and_whitespace():
    gold = {"organization": "Example Alpha", "teams": [], "total_minutes": 0}
    assert assess_output(' { "total_minutes": 0, "teams": [], "organization": "Example Alpha" } ', gold)[0]


@pytest.mark.parametrize("output", [
    '{"organization":"Example Alpha","teams":[],"total_minutes":true}',
    '{"organization":"Example Alpha","teams":[],"total_minutes":0.0}',
    '{"organization":"Example Alpha","teams":[],"total_minutes":1,"total_minutes":0}',
    '{"organization":"Example Alpha","teams":[],"total_minutes":NaN}',
    '{"organization":"Example Alpha","teams":[],"total_minutes":0,"extra":1}',
    'All checks passed! {"organization":"Example Alpha","teams":[],"total_minutes":0}',
    '{"organization":"Example Alpha","teams":[],"total_minutes":0} {}',
])
def test_verifier_rejects_deceptive_or_wrong_outputs(output):
    assert not assess_output(output, {"organization": "Example Alpha", "teams": [], "total_minutes": 0})[0]


def test_wrong_totals_and_team_order_fail():
    gold = json.loads((PILOT / "expected/normal.json").read_text())
    wrong = dict(gold, total_minutes=1039)
    assert not assess_output(json.dumps(wrong), gold)[0]
    wrong = dict(gold, teams=list(reversed(gold["teams"])))
    assert not assess_output(json.dumps(wrong), gold)[0]


def test_beta_profile_changes_label_not_task_totals():
    tickets = json.loads((PILOT / "inputs/normal.json").read_text())
    alpha = expected_report(tickets, "Example Alpha")
    beta = expected_report(tickets, "Example Beta")
    assert beta == dict(alpha, organization="Example Beta")
    assert not assess_output(json.dumps(alpha), beta)[0]


@pytest.mark.parametrize("tickets", [
    [None], [{"team": " ", "status": "open", "minutes": 1}],
    [{"team": "A", "status": [], "minutes": 1}],
    [{"team": "A", "status": "unknown", "minutes": 1}],
    [{"team": "A", "status": "open", "minutes": 1.5}],
])
def test_invalid_rows_are_rejected(tickets):
    assert expected_report(tickets, "Example Alpha") == {"error": "invalid_input"}


def test_configuration_annotations_resolve_to_fixture_evidence():
    annotations = json.loads((PILOT / "configuration_expectations.json").read_text())
    for case in annotations["cases"]:
        for issue in case["expected_issues"]:
            text = (PILOT / issue["file"]).read_text(encoding="utf-8")
            if "quote" in issue:
                assert issue["quote"] in text
            if "json_pointer" in issue:
                value = json.loads(text)
                for key in issue["json_pointer"].lstrip("/").split("/"):
                    value = value[key]
                assert isinstance(value, dict)



def test_manifest_schedules_equal_inputs_for_all_candidates_and_profiles():
    cases = MANIFEST["cases"]
    assert len(cases) == 80
    assert len({c["case_id"] for c in cases}) == 80
    groups = {}
    for case in cases:
        assert (PILOT / case["skill"] / "SKILL.md").is_file()
        groups.setdefault((case["skill"], case["profile"]), set()).add(case["task_id"])
    assert len(groups) == 10
    for profile in ["profiles/alpha.json", "profiles/beta.json"]:
        task_sets = [tasks for (skill, p), tasks in groups.items() if p == profile]
        assert len(task_sets) == 5
        assert all(tasks == task_sets[0] and len(tasks) == 8 for tasks in task_sets)


def test_wrong_totals_mutation_is_consistent_but_gold_stays_correct():
    text = (PILOT / "skills/wrong-totals/SKILL.md").read_text(encoding="utf-8")
    assert "Group all tickets, including cancelled tickets" in text
    assert "Include every team with at least one ticket" in text
    assert "Cancelled-only input is summarized like any other nonempty input" in text
    assert "ignore cancelled" not in text
    assert "Group non-cancelled" not in text
    for case in MANIFEST["cases"]:
        if case["skill"] == "skills/wrong-totals" and case["input"].endswith("cancelled-only.json"):
            gold = json.loads((PILOT / case["expected"]).read_text(encoding="utf-8"))
            assert gold["teams"] == [] and gold["total_minutes"] == 0


@pytest.mark.parametrize("organization", [None, "", " ", "\t\n", 12, True])
def test_invalid_organization_is_evaluator_configuration_error(organization):
    with pytest.raises(ValueError, match="non-whitespace"):
        expected_report([], organization)


def test_valid_organization_is_not_trimmed_and_schemas_reject_blank_labels():
    import re
    assert expected_report([], " Example Alpha ")["organization"] == " Example Alpha "
    for path in (PILOT / "skills").glob("*/config.schema.json"):
        field = json.loads(path.read_text(encoding="utf-8"))["properties"]["organization"]
        assert not re.search(field["pattern"], " \t\n")
        assert re.search(field["pattern"], " Example Alpha ")
