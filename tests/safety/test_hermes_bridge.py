import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from metaskill.evaluate.models import SafetyTier, Skill
from metaskill.evaluate.safety import hermes_bridge
from metaskill.evaluate.safety.findings import BackendStatus
from metaskill.evaluate.safety.hermes_bridge import UnsupportedRequest, translate

SCHEMA = {"type": "object", "required": ["verdict"], "properties": {"verdict": {"type": "string"}}}


# --- Translating Cisco's request for ctx.llm --------------------------------------


def test_system_and_user_messages_become_instructions_and_input():
    kwargs = translate(
        [{"role": "system", "content": "You audit skills."},
         {"role": "user", "content": "Here is the skill."}],
        {"response_format": {"type": "json_schema", "json_schema": {"schema": SCHEMA}},
         "temperature": 0.0, "max_tokens": 8192})
    assert kwargs["instructions"].startswith("You audit skills.")
    assert kwargs["input"] == [{"type": "text", "text": "Here is the skill."}]
    assert (kwargs["temperature"], kwargs["max_tokens"]) == (0.0, 8192)


def test_schema_is_guidance_not_strict_validation():
    # ctx.llm fails the whole call on a strict schema mismatch; Cisco's parser repairs small gaps.
    kwargs = translate([{"role": "user", "content": "x"}],
                       {"response_format": {"type": "json_schema", "json_schema": {"schema": SCHEMA}}})
    assert "json_schema" not in kwargs
    assert kwargs["json_mode"] is True
    assert '"required": ["verdict"]' in kwargs["instructions"]


def test_json_object_format_becomes_json_mode():
    kwargs = translate([{"role": "user", "content": "x"}], {"response_format": {"type": "json_object"}})
    assert kwargs["json_mode"] is True


def test_list_content_with_text_parts():
    kwargs = translate([{"role": "user", "content": [{"type": "text", "text": "a"},
                                                     {"type": "text", "text": "b"}]}], {})
    assert kwargs["input"] == [{"type": "text", "text": "a\nb"}]


@pytest.mark.parametrize("messages, params", [
    ([{"role": "user", "content": "x"}], {"tools": [{"name": "t"}]}),
    ([{"role": "user", "content": "x"}], {"stream": True}),
    ([{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}], {}),
    ([{"role": "user", "content": [{"type": "image_url", "image_url": "..."}]}], {}),
    ([{"role": "system", "content": "only instructions"}], {}),
])
def test_requests_ctx_llm_cannot_express_are_refused(messages, params):
    with pytest.raises(UnsupportedRequest):
        translate(messages, params)


# --- End to end with the real scanner and a fake ctx.llm --------------------------------


def minimal(schema):
    """The smallest answer that fits a JSON schema, like a terse model's reply."""
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        return {k: minimal(schema["properties"][k]) for k in schema.get("required", [])}
    return {"array": [], "string": "none", "number": 0, "integer": 0, "boolean": False}.get(kind)


@dataclass
class FakeUsage:
    input_tokens: int = 3000
    output_tokens: int = 50


@dataclass
class FakeResult:
    parsed: object
    text: str = ""
    provider: str = "fake"
    model: str = "fake-model"
    usage: FakeUsage = field(default_factory=FakeUsage)


class FakeLlm:
    """Stands in for Hermes's ctx.llm: records each call, answers within the schema."""

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("Hermes: model unavailable")
        schema = json.loads(kwargs["instructions"].split("JSON schema:\n", 1)[1])
        return FakeResult(parsed=minimal(schema))


@pytest.fixture
def tiny_skill(tmp_path):
    (tmp_path / "SKILL.md").write_text(
        "---\nname: hello\ndescription: Say hello to the user.\n---\n\n# Hello\n\nGreet the user.\n")
    return Skill(path=tmp_path, name="hello", body="")


def test_cisco_llm_analyzer_runs_through_ctx_llm(tiny_skill):
    pytest.importorskip("skill_scanner")
    from metaskill.evaluate.safety.cisco import CiscoBackend

    llm = FakeLlm()
    result = CiscoBackend(llm).scan(tiny_skill, SafetyTier.DEEP)
    assert result.status is BackendStatus.OK, result.detail
    assert result.tier is SafetyTier.DEEP
    [call] = llm.calls
    assert call["instructions"] and call["input"]
    assert call["json_mode"] is True
    assert call["purpose"] == hermes_bridge.PURPOSE


def test_cisco_calls_go_through_the_judges_model_setting(tiny_skill):
    pytest.importorskip("skill_scanner")
    from metaskill.evaluate.safety.cisco import CiscoBackend

    llm = FakeLlm()
    CiscoBackend(llm, task="metaskill_judge").scan(tiny_skill, SafetyTier.DEEP)
    assert [c["task"] for c in llm.calls] == ["metaskill_judge"]


def test_without_a_task_the_active_model_is_used(tiny_skill):
    pytest.importorskip("skill_scanner")
    from metaskill.evaluate.safety.cisco import CiscoBackend

    llm = FakeLlm()
    CiscoBackend(llm).scan(tiny_skill, SafetyTier.DEEP)
    assert "task" not in llm.calls[0]


def test_ctx_llm_failure_falls_back_to_static(tiny_skill):
    pytest.importorskip("skill_scanner")
    from metaskill.evaluate.safety.cisco import CiscoBackend

    result = CiscoBackend(FakeLlm(fail=True)).scan(tiny_skill, SafetyTier.DEEP)
    assert result.status is BackendStatus.LLM_UNAVAILABLE
    assert result.tier is SafetyTier.STATIC
    assert "model unavailable" in result.detail


def test_registration_is_removed_after_the_scan(tiny_skill):
    pytest.importorskip("litellm")
    llm = FakeLlm()
    with hermes_bridge.routed(llm, "metaskill_judge") as model:
        assert model.startswith(f"{hermes_bridge.PROVIDER}/")
        assert hermes_bridge._llm_for(model) == (llm, "metaskill_judge")
    with pytest.raises(RuntimeError):
        hermes_bridge._llm_for(model)


def test_provider_is_added_once_without_removing_others():
    litellm = pytest.importorskip("litellm")
    other = {"provider": "someone-else", "custom_handler": object()}
    litellm.custom_provider_map = [other]
    hermes_bridge._handler = None
    with hermes_bridge.routed(FakeLlm()):
        pass
    with hermes_bridge.routed(FakeLlm()):
        pass
    names = [p["provider"] for p in litellm.custom_provider_map]
    assert names == ["someone-else", hermes_bridge.PROVIDER]


# --- Retrying a reply that does not fit the schema -------------------------------------


class ScriptedLlm:
    """Returns the given parsed replies in order (None means "not JSON")."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResult(parsed=self.replies.pop(0), usage=FakeUsage(1000, 100))


BASE = {"instructions": "Audit this.", "input": [{"type": "text", "text": "skill"}], "json_mode": True}


def test_valid_reply_is_not_retried():
    pytest.importorskip("jsonschema")
    llm = ScriptedLlm({"verdict": "SAFE"})
    result, tokens = hermes_bridge.complete_with_retry(llm, BASE, SCHEMA)
    assert result.parsed == {"verdict": "SAFE"}
    assert len(llm.calls) == 1
    assert tokens == (1000, 100)


def test_missing_field_is_retried_once_naming_the_problem():
    pytest.importorskip("jsonschema")
    llm = ScriptedLlm({}, {"verdict": "SAFE"})
    result, tokens = hermes_bridge.complete_with_retry(llm, BASE, SCHEMA)
    assert result.parsed == {"verdict": "SAFE"}
    assert len(llm.calls) == 2
    assert "'verdict' is a required property" in llm.calls[1]["instructions"]
    assert llm.calls[1]["input"] == BASE["input"]
    assert tokens == (2000, 200)  # both calls are counted


def test_still_invalid_after_the_retry_is_returned_for_cisco_to_reject():
    pytest.importorskip("jsonschema")
    llm = ScriptedLlm({}, {"verdict": 3})
    result, _ = hermes_bridge.complete_with_retry(llm, BASE, SCHEMA)
    assert result.parsed == {"verdict": 3}
    assert len(llm.calls) == 1 + hermes_bridge.MAX_RETRIES


def test_reply_that_is_not_json_is_retried():
    pytest.importorskip("jsonschema")
    llm = ScriptedLlm(None, {"verdict": "SAFE"})
    hermes_bridge.complete_with_retry(llm, BASE, SCHEMA)
    assert "not valid JSON" in llm.calls[1]["instructions"]


def test_no_schema_means_no_check():
    llm = ScriptedLlm({"anything": True})
    hermes_bridge.complete_with_retry(llm, BASE, None)
    assert len(llm.calls) == 1


def test_problem_list_is_capped():
    pytest.importorskip("jsonschema")
    schema = {"type": "object", "required": [f"f{i}" for i in range(9)],
              "properties": {f"f{i}": {"type": "string"} for i in range(9)}}
    problems = hermes_bridge.schema_problems({}, schema)
    assert len(problems) == hermes_bridge.MAX_PROBLEMS_LISTED + 1
    assert problems[-1] == "and 4 more problems"
