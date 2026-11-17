"""A deterministic Classifier for tests and CI, so no test needs a model or a network.

    from fake_classifier import FakeClassifier

FakeClassifier() answers with simple rules (rule_based); pass `respond` to script the
answers instead, or `error` to make every call raise. Every request it receives is kept
in `received`, so a test can check exactly what a real provider would have been sent.
"""

from __future__ import annotations

from collections.abc import Callable

from metaskill.generalize.classify import Classification, ClassificationRequest, Label
from metaskill.generalize.detect import Category

_EXAMPLE_MARKERS = ("example", "sample", "my-", "your", "path/to", "foo", "demo")


def rule_based(request: ClassificationRequest) -> Classification:
    """Example-looking values are EXAMPLE, internal hosts ORGANIZATION, the rest UNCERTAIN."""
    value = request.value.lower()
    if any(marker in value for marker in _EXAMPLE_MARKERS):
        return Classification(request.finding_id, Label.EXAMPLE)
    if request.category is Category.ORGANIZATION_HOST:
        return Classification(request.finding_id, Label.ORGANIZATION, True, "organization_host")
    return Classification(request.finding_id, Label.UNCERTAIN)


class FakeClassifier:
    name = "fake"

    def __init__(self, respond: Callable[[list[ClassificationRequest]], object] | None = None,
                 error: Exception | None = None):
        self.respond = respond
        self.error = error
        self.received: list[ClassificationRequest] = []
        self.calls = 0

    def classify(self, requests):
        self.calls += 1
        self.received.extend(requests)
        if self.error is not None:
            raise self.error
        if self.respond is not None:
            return self.respond(requests)
        return [rule_based(r) for r in requests]
