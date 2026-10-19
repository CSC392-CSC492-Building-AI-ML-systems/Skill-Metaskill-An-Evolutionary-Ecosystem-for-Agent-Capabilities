"""The classifier interface in metaskill/generalize/classify.py, with a fake classifier."""

from metaskill.generalize.classify import (
    Classification,
    ClassificationRequest,
    Label,
    build_requests,
    classify,
)
from metaskill.generalize.detect import REDACTED, Category, scan_skill

GITHUB_TOKEN = "ghp_" + "A1b2C3d4E5f6" * 3
GENERIC_SECRET = "abc123def456ghi789"


class FakeClassifier:
    def __init__(self, respond):
        self.respond = respond
        self.received: list[ClassificationRequest] = []

    def classify(self, requests):
        self.received.extend(requests)
        return self.respond(requests)


def make_skill(tmp_path):
    (tmp_path / "SKILL.md").write_text(
        "# Deploy\n"
        "Ask jayant@company.com before deploying.\n"
        f"export GITHUB_TOKEN={GITHUB_TOKEN}\n"
        "Clone https://github.com/atlas/streams\n",
        encoding="utf-8")
    (tmp_path / "run.py").write_text(
        f'API_KEY = "{GENERIC_SECRET}"\n'
        'key = os.getenv("API_KEY")\n'
        'OUTPUT_PATH = "/Users/jayant/out"\n',
        encoding="utf-8")
    return scan_skill(tmp_path)


def test_classifier_never_receives_credentials(tmp_path):
    result = make_skill(tmp_path)
    fake = FakeClassifier(lambda requests: [])
    classify(result, fake)
    assert {r.category for r in fake.received} == {
        Category.EMAIL, Category.REPOSITORY, Category.FILE_PATH}
    sent = repr(fake.received)
    assert GITHUB_TOKEN not in sent and GENERIC_SECRET not in sent
    assert any(REDACTED in r.context for r in fake.received)


def test_requests_carry_value_name_and_nearby_lines(tmp_path):
    result = make_skill(tmp_path)
    path = next(r for r in build_requests(result, context_lines=1)
                if r.category is Category.FILE_PATH)
    assert (path.file, path.line, path.value, path.name) == (
        "run.py", 3, "/Users/jayant/out", "OUTPUT_PATH")
    assert path.context == 'key = os.getenv("API_KEY")\nOUTPUT_PATH = "/Users/jayant/out"\n'


def test_valid_labels_are_kept(tmp_path):
    result = make_skill(tmp_path)

    def respond(requests):
        return [Classification(r.finding_id, Label.PERSONAL, True, "contact_email")
                for r in requests if r.category is Category.EMAIL]

    labels = classify(result, FakeClassifier(respond))
    email = next(f for f in result.findings if f.category is Category.EMAIL)
    assert labels[email.id] == Classification(email.id, Label.PERSONAL, True, "contact_email")


def test_bad_or_missing_responses_become_uncertain(tmp_path):
    result = make_skill(tmp_path)
    credential = next(f for f in result.findings if f.category is Category.CREDENTIAL)

    def respond(requests):
        first, second, *_ = requests
        return [
            Classification(first.finding_id, "not-a-label", True),
            Classification(second.finding_id, Label.ORGANIZATION, "yes", "Bad Key!"),
            Classification(credential.id, Label.REUSABLE),  # was never asked about
            "garbage",
        ]

    labels = classify(result, FakeClassifier(respond))
    assert credential.id not in labels
    first, second, *rest = labels.values()
    assert first.label is Label.UNCERTAIN and not first.replace
    assert second == Classification(second.finding_id, Label.ORGANIZATION, False, "")
    assert all(c.label is Label.UNCERTAIN for c in rest)


def test_nothing_to_classify_does_not_call_the_classifier(tmp_path):
    (tmp_path / "SKILL.md").write_text(f"export TOKEN={GITHUB_TOKEN}\n", encoding="utf-8")
    fake = FakeClassifier(lambda requests: [])
    assert classify(scan_skill(tmp_path), fake) == {}
    assert fake.received == []
