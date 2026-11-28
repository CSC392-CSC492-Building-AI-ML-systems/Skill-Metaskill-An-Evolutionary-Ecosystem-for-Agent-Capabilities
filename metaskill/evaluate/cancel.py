"""Stopping a running evaluation (docs/evaluate/runner.md 2.7).

Ctrl-C does not reach an evaluation: Hermes runs `/metaskill` in its gateway, on an RPC thread,
and gives a plugin command no interrupt signal. When the CLI lets go, the gateway runs the
command to its end, sessions and model calls included. So an evaluation registers here while
it runs, and is stopped by:

- `/metaskill stop` (stop_all): every evaluation of the same Hermes profile in this process;
- Hermes' own interrupt flag (tools.interrupt) for the thread that started it, set when the
  user interrupts an agent turn whose `metaskill` tool call started the evaluation.

The runner watches for either (watch) and stops as on Ctrl-C: no new session, every container
removed, the agent's Hermes processes stopped; then EvaluationStopped ends the call.
"""

from __future__ import annotations

import contextvars
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

POLL = 0.5  # seconds between checks while a runner call runs


class EvaluationStopped(Exception):
    """The evaluation was stopped (/metaskill stop, or Hermes interrupted the turn)."""


@dataclass
class Run:
    """One evaluation in progress."""

    owner: str  # the Hermes profile it runs for: only that profile's stop reaches it
    thread_id: int  # the thread that started it, whose Hermes interrupt flag stops it
    event: threading.Event = field(default_factory=threading.Event)
    # A flag already set when the evaluation started is stale (an earlier turn's): only a
    # flag raised after the start counts.
    interrupted_at_start: bool = False
    reason: str = ""

    def stop(self, reason: str) -> None:
        if not self.event.is_set():
            self.reason = reason
            self.event.set()

    def requested(self) -> bool:
        if self.event.is_set():
            return True
        if not self.interrupted_at_start and _hermes_interrupted(self.thread_id):
            self.stop("Hermes interrupted the turn")
            return True
        return False


_lock = threading.Lock()
_runs: dict[int, Run] = {}
_current: contextvars.ContextVar[Run | None] = contextvars.ContextVar("metaskill_run", default=None)


@contextmanager
def running() -> Iterator[Run]:
    """Register the evaluation the calling thread runs, for as long as the block lasts."""
    tid = threading.get_ident()
    run = Run(owner=_owner(), thread_id=tid, interrupted_at_start=_hermes_interrupted(tid))
    with _lock:
        _runs[id(run)] = run
    token = _current.set(run)
    try:
        yield run
    finally:
        _current.reset(token)
        with _lock:
            _runs.pop(id(run), None)


def current() -> Run | None:
    return _current.get()


def check() -> None:
    """Raise EvaluationStopped if this thread's evaluation was asked to stop."""
    run = _current.get()
    if run is not None and run.requested():
        raise EvaluationStopped(run.reason or "the evaluation was stopped")


def stop_all() -> int:
    """Ask every evaluation of the calling Hermes profile to stop; how many were running."""
    owner = _owner()
    with _lock:
        mine = [run for run in _runs.values() if run.owner == owner]
    for run in mine:
        run.stop("stopped with /metaskill stop")
    return len(mine)


class Watcher:
    """Calls `stop` once, from a thread of its own, when `run` is asked to stop; the runner's
    threads may all be blocked on sessions meanwhile."""

    def __init__(self, run: Run, stop: Callable[[], None], poll: float = POLL):
        self._run, self._stop, self._poll = run, stop, poll
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="metaskill-stop-watch",
                                        daemon=True)
        self._thread.start()

    def _watch(self) -> None:
        while not self._done.wait(self._poll):
            if self._run.requested():
                self._stop()
                return

    def close(self) -> None:
        self._done.set()
        self._thread.join(timeout=5)


def watch(stop: Callable[[], None]) -> Watcher | None:
    """A Watcher for the calling thread's evaluation, or None outside one."""
    run = _current.get()
    return Watcher(run, stop) if run is not None else None


def _owner() -> str:
    """The Hermes profile this thread serves: the profile secret scope's home on a gateway
    that serves several (agent.secret_scope), else the process's HERMES_HOME."""
    try:
        from agent.secret_scope import current_secret_scope_home

        home = current_secret_scope_home()
    except Exception:  # not in Hermes, or its internals changed
        home = None
    return str(home or os.environ.get("HERMES_HOME", ""))


def _hermes_interrupted(thread_id: int) -> bool:
    """Whether Hermes flagged `thread_id` as interrupted (its tools check the same flag).
    False outside Hermes, or if the module changed."""
    try:
        from tools.interrupt import is_thread_interrupted

        return bool(is_thread_interrupted(thread_id))
    except Exception:
        return False
