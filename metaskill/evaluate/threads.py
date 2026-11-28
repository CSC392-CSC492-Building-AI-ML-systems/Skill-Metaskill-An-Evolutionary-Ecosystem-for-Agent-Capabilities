"""Work handed to threads keeps the caller's context (runner.md 2.7).

Hermes keeps the active profile's credentials in a contextvar (agent/secret_scope.py) and,
on a multiplexing gateway, refuses a credential read made outside it ("Hermes could not
read this profile's ANTHROPIC_TOKEN"). A ThreadPoolExecutor thread starts with an empty
context, so a judge call from one failed there, for every run. Hermes' own answer is to run
the work in a copy of the caller's context (contextvars.copy_context), which is what these
do. A copy per task: one Context cannot be entered by two threads at once.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable, Iterable
from concurrent.futures import Executor, Future
from typing import Any, TypeVar

T = TypeVar("T")
R = TypeVar("R")


def submit(executor: Executor, fn: Callable[..., R], *args: Any, **kwargs: Any) -> Future[R]:
    """executor.submit, with `fn` run in a copy of the calling thread's context."""
    return executor.submit(contextvars.copy_context().run, fn, *args, **kwargs)


def map_in_context(executor: Executor, fn: Callable[[T], R], items: Iterable[T]) -> list[R]:
    """list(executor.map(fn, items)), each call in a copy of the caller's context: results
    in the items' order, and the first item's exception, in order, is raised."""
    futures = [submit(executor, fn, item) for item in items]
    return [future.result() for future in futures]
