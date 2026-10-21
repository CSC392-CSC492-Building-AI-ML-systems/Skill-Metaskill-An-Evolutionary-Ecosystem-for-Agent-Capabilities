"""Routes Cisco's LLM analyzer through Hermes's plugin LLM access (`ctx.llm`).

Cisco's LLM analyzer calls models through LiteLLM and wants a model, a key and a
URL. Hermes never gives plugins keys: they ask Hermes for a completion through
`ctx.llm`, and Hermes handles auth. This module registers a LiteLLM custom
provider whose handler forwards each of Cisco's requests to `ctx.llm`, so Cisco
only ever gets a placeholder key (docs/safety/cisco.md, "How Cisco reaches the model").
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Protocol

PROVIDER = "metaskill_hermes"
# Cisco refuses to start its LLM analyzer without some API key value. This one is
# never used or sent anywhere: requests for PROVIDER go to the handler below.
PLACEHOLDER_KEY = "not-a-key"
PURPOSE = "metaskill safety evaluation: Cisco skill-scanner LLM analyzer"

# A reply that does not fit Cisco's schema is asked for once more, naming what was
# wrong. ctx.llm cannot ask the provider to enforce a schema (it sends strict=False),
# so a small model sometimes misses a required field; Cisco then rejects the reply.
MAX_RETRIES = 1
MAX_PROBLEMS_LISTED = 5

# LiteLLM parameters we pass on to ctx.llm. Anything else Cisco sends that would
# change the request's meaning (tools, streaming) is refused rather than dropped.
_UNSUPPORTED = ("tools", "tool_choice", "functions", "function_call")


class PluginLlm(Protocol):
    """The part of Hermes's `ctx.llm` (agent.plugin_llm.PluginLlm) this module uses."""

    def complete_structured(self, *, instructions: str, input: list[dict[str, Any]],
                            json_schema: Any = None, json_mode: bool = False,
                            temperature: float | None = None, max_tokens: int | None = None,
                            timeout: float | None = None, purpose: str | None = None) -> Any: ...


class UnsupportedRequest(ValueError):
    """Cisco sent something ctx.llm cannot express (tools, multi-turn history)."""


_llms: dict[str, PluginLlm] = {}
_lock = threading.Lock()
_handler: Any = None


@contextmanager
def routed(llm: PluginLlm) -> Iterator[str]:
    """Make `llm` reachable by Cisco for the duration of a scan.

    Yields the model name to give Cisco. Each scan gets its own name, so
    concurrent scans with different `ctx.llm` objects do not mix.
    """
    _install()
    token = uuid.uuid4().hex
    with _lock:
        _llms[token] = llm
    try:
        yield f"{PROVIDER}/{token}"
    finally:
        with _lock:
            _llms.pop(token, None)


def translate(messages: list[dict[str, Any]], params: dict[str, Any]) -> dict[str, Any]:
    """One LiteLLM chat request as keyword arguments for ctx.llm.complete_structured."""
    for name in _UNSUPPORTED:
        if params.get(name):
            raise UnsupportedRequest(f"ctx.llm cannot take `{name}`")
    if params.get("stream"):
        raise UnsupportedRequest("ctx.llm does not stream")

    instructions, inputs = [], []
    for message in messages:
        role = message.get("role")
        text = _text(message.get("content"))
        if role == "system":
            instructions.append(text)
        elif role == "user":
            inputs.append({"type": "text", "text": text})
        else:
            raise UnsupportedRequest(f"ctx.llm takes one instruction and input, not a `{role}` turn")
    if not inputs:
        raise UnsupportedRequest("request has no user content")

    text = "\n\n".join(instructions) or "Answer the request."
    response_format = params.get("response_format") or {}
    json_mode = response_format.get("type") in ("json_schema", "json_object")
    schema = (response_format.get("json_schema") or {}).get("schema")
    if schema is not None:
        # The schema goes in as guidance, not as ctx.llm's json_schema. ctx.llm
        # validates strictly and fails the call when the model misses a field
        # (measured: 10 of 25 Cisco scans on 20 Oct 2026). Cisco's own parser
        # checks the reply and repairs small gaps, as it does with a direct provider.
        text += "\n\nJSON schema:\n" + json.dumps(schema, ensure_ascii=False, sort_keys=True)
    kwargs: dict[str, Any] = {"instructions": text, "input": inputs, "purpose": PURPOSE}
    if json_mode:
        kwargs["json_mode"] = True
    for name in ("temperature", "max_tokens", "timeout"):
        if params.get(name) is not None:
            kwargs[name] = params[name]
    return kwargs


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
            else:
                raise UnsupportedRequest("only text content can be forwarded to ctx.llm")
        return "\n".join(parts)
    raise UnsupportedRequest(f"unexpected message content: {type(content).__name__}")


def _llm_for(model: str) -> PluginLlm:
    token = model.split("/", 1)[1] if "/" in model else model
    with _lock:
        llm = _llms.get(token)
    if llm is None:
        raise RuntimeError(f"no Hermes LLM registered for {model!r}; is the scan still running?")
    return llm


def _install() -> None:
    """Register the provider with LiteLLM once, alongside any others."""
    global _handler
    import litellm
    from litellm import CustomLLM, ModelResponse

    with _lock:
        if _handler is not None:
            return

        class _Handler(CustomLLM):
            def completion(self, model: str, messages: list, *args: Any,
                           optional_params: dict | None = None, **kwargs: Any) -> Any:
                params = optional_params or {}
                result, usage = complete_with_retry(
                    _llm_for(model), translate(messages, params), _schema(params))
                return _response(ModelResponse, result, usage)

            async def acompletion(self, model: str, messages: list, *args: Any,
                                  optional_params: dict | None = None, **kwargs: Any) -> Any:
                # The sync method in a worker thread: ctx.llm's async API expects
                # Hermes's own event loop, and Cisco runs its own.
                return await asyncio.to_thread(
                    self.completion, model, messages, optional_params=optional_params)

        _handler = _Handler()
        providers = list(getattr(litellm, "custom_provider_map", None) or [])
        if not any(p.get("provider") == PROVIDER for p in providers):
            providers.append({"provider": PROVIDER, "custom_handler": _handler})
        litellm.custom_provider_map = providers


def complete_with_retry(llm: PluginLlm, kwargs: dict[str, Any],
                        schema: Any) -> tuple[Any, tuple[int, int]]:
    """Call ctx.llm, and ask again once if the reply does not fit the schema.

    Returns the last reply and the (input, output) tokens of every call made.
    A reply that still does not fit is returned as is: Cisco then rejects it and
    the scan falls back to static, as for any other LLM failure.
    """
    tokens = [0, 0]
    result = _call(llm, kwargs, tokens)
    for _ in range(MAX_RETRIES):
        problems = schema_problems(getattr(result, "parsed", None), schema)
        if not problems:
            break
        retry = dict(kwargs)
        retry["instructions"] = kwargs["instructions"] + (
            "\n\nYour previous reply did not match the JSON schema:\n- "
            + "\n- ".join(problems)
            + "\nReply again with one complete JSON object that matches the schema exactly.")
        result = _call(llm, retry, tokens)
    return result, (tokens[0], tokens[1])


def schema_problems(parsed: Any, schema: Any) -> list[str]:
    """What is wrong with a reply, in short lines for the model; [] if it fits.

    With no schema, or without the jsonschema package, nothing is checked.
    """
    if schema is None:
        return []
    if parsed is None:
        return ["the reply was not valid JSON"]
    try:
        import jsonschema
    except ImportError:
        return []
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(parsed),
                    key=lambda e: list(e.absolute_path))
    problems = []
    for error in errors[:MAX_PROBLEMS_LISTED]:
        where = "/".join(str(p) for p in error.absolute_path) or "top level"
        problems.append(f"at {where}: {error.message}")
    if len(errors) > MAX_PROBLEMS_LISTED:
        problems.append(f"and {len(errors) - MAX_PROBLEMS_LISTED} more problems")
    return problems


def _call(llm: PluginLlm, kwargs: dict[str, Any], tokens: list[int]) -> Any:
    result = llm.complete_structured(**kwargs)
    usage = getattr(result, "usage", None)
    tokens[0] += int(getattr(usage, "input_tokens", 0) or 0)
    tokens[1] += int(getattr(usage, "output_tokens", 0) or 0)
    return result


def _schema(params: dict[str, Any]) -> Any:
    response_format = params.get("response_format") or {}
    return (response_format.get("json_schema") or {}).get("schema")


def _response(model_response: Any, result: Any, tokens: tuple[int, int]) -> Any:
    """ctx.llm's result as the LiteLLM response Cisco expects; tokens cover any retry."""
    parsed = getattr(result, "parsed", None)
    text = json.dumps(parsed) if parsed is not None else (getattr(result, "text", "") or "")
    prompt, completion = tokens
    return model_response(
        model=f"{getattr(result, 'provider', '')}/{getattr(result, 'model', '')}".strip("/"),
        choices=[{"message": {"role": "assistant", "content": text}}],
        usage={"prompt_tokens": prompt, "completion_tokens": completion,
               "total_tokens": prompt + completion},
    )
