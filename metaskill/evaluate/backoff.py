"""Backing off when the model provider rate-limits (docs/evaluate/runner.md 2.7).

Task runs can run in parallel (EvaluationConfig.max_parallel_runs), so one provider can see
several sessions and judge calls at once. When one of them is rate-limited, a `Backoff`
shared by the whole runner call pauses every worker before its next model use, doubling the
pause while limits keep coming, and the call that was limited is retried. A run that is
still limited after `attempts` tries is an error, as any provider failure is: it says
nothing about the skill.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

R = TypeVar("R")

# Seconds of the first pause, and the longest one: a provider's rate-limit window is
# usually a minute, so the pauses reach it in a few doublings and stop there.
INITIAL_DELAY = 5.0
MAX_DELAY = 60.0
# Tries per call (the first one included) before a rate limit is reported as an error.
ATTEMPTS = 4
# Up to this many seconds more, at random, per worker once a pause ends: the workers a
# pause held would otherwise all call the provider in the same instant, and likely be
# limited again together.
JITTER = 1.0
# Doublings at most: MAX_DELAY is reached long before, and 2 ** streak cannot overflow.
_MAX_STREAK = 32

# How a rate limit shows itself when a provider SDK or Hermes does not say so in a field.
_RATE_LIMIT_WORDS = ("rate limit", "rate_limit", "ratelimit", "too many requests")
_RATE_LIMIT_STATUS = 429


def is_rate_limited(error: BaseException) -> bool:
    """Whether `error`, or any exception it was raised from, is a provider rate limit:
    flagged as one (`rate_limited`), an HTTP 429 (`status_code` or `status`), a class
    named like one (e.g. RateLimitError), or a message saying so. An exception that says
    either way in `rate_limited` is taken at its word, and its message is not read: it can
    quote text that is not the provider's."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        flag = getattr(current, "rate_limited", None)
        if isinstance(flag, bool):
            return flag
        for name in ("status_code", "status"):
            if getattr(current, name, None) == _RATE_LIMIT_STATUS:
                return True
        if any("ratelimit" in cls.__name__.lower() for cls in type(current).__mro__):
            return True
        text = str(current).lower()
        if any(word in text for word in _RATE_LIMIT_WORDS):
            return True
        current = current.__cause__ or current.__context__
    return False


class Backoff:
    """A pause shared by every worker of one runner call.

    `wait()` is called before each model use (a session, a judge call) and returns once no
    pause is in force. `limited()` records a rate limit and starts or extends the pause:
    INITIAL_DELAY, doubled for each further pause before a call succeeds, at most MAX_DELAY.
    A limit while a pause is in force is the same burst (16 workers limited by one window)
    and leaves it as it is; only a limit after the pause has ended doubles it.
    `succeeded()` resets the doubling. `cancel()` ends every pause at once, and every later
    one, so a stopping runner does not wait up to MAX_DELAY per worker for its threads.
    Thread-safe; `sleep` and `clock` are for tests (None: an interruptible wait).
    """

    def __init__(self, *, initial: float = INITIAL_DELAY, maximum: float = MAX_DELAY,
                 attempts: int = ATTEMPTS, sleep: Callable[[float], None] | None = None,
                 clock: Callable[[], float] = time.monotonic, jitter: float = JITTER):
        if attempts < 1:
            raise ValueError(f"attempts must be >= 1, got {attempts}")
        self.initial, self.maximum, self.attempts = initial, maximum, attempts
        self._cancelled = threading.Event()
        self._given_sleep = sleep
        # The default waits on the event, so cancel() wakes a waiting worker at once.
        self._sleep = sleep if sleep is not None else self._cancelled.wait
        self._clock = clock
        self.jitter = jitter
        self._pause = _Pause()

    def linked(self) -> Backoff:
        """A Backoff that shares this one's pause (a rate limit seen by either pauses both)
        but has a cancel() of its own. evaluate keeps one pause for its whole call, so the
        dimensions it scores in the background wait out the limits the task runs hit; each
        runner call stops only its own workers, not the scoring of the skills after it."""
        other = Backoff(initial=self.initial, maximum=self.maximum, attempts=self.attempts,
                        sleep=self._given_sleep, clock=self._clock, jitter=self.jitter)
        other._pause = self._pause
        return other

    @property
    def limits(self) -> int:
        """Every rate limit seen on this pause, by any linked Backoff, for the log."""
        with self._pause.lock:
            return self._pause.limits

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        """End every pause now and later: the call is stopping (runner.md 2.7)."""
        self._cancelled.set()

    def wait(self) -> None:
        paused = False
        while not self._cancelled.is_set():
            with self._pause.lock:
                left = self._pause.resume_at - self._clock()
            if left <= 0:
                if paused and self.jitter > 0 and not self._cancelled.is_set():
                    self._sleep(random.uniform(0, self.jitter))
                return
            paused = True
            self._sleep(left)

    def limited(self) -> float:
        """Record a rate limit; returns the length of the pause in force."""
        pause = self._pause
        with pause.lock:
            pause.limits += 1
            now = self._clock()
            if now >= pause.resume_at:  # not covered by the pause in force: a longer one
                pause.delay = min(self.maximum, self.initial * 2 ** pause.streak)
                pause.streak = min(pause.streak + 1, _MAX_STREAK)
                pause.resume_at = now + pause.delay
            return pause.delay

    def succeeded(self) -> None:
        with self._pause.lock:
            self._pause.streak = 0

    def call(self, fn: Callable[[], R]) -> R:
        """fn(), after any pause in force; made again, up to `attempts` times in all, while
        it raises a rate limit (is_rate_limited). Raises BackoffCancelled, without calling
        fn, once cancel() was called; any other error of fn's as it is."""
        for attempt in range(1, self.attempts + 1):
            self.wait()
            if self.cancelled:
                raise BackoffCancelled("the evaluation was stopped before this model call")
            try:
                result = fn()
            except Exception as e:
                if attempt == self.attempts or not is_rate_limited(e):
                    raise
                if self.cancelled:  # stopped: no retry, and no longer pause for others
                    raise
                self.limited()
                continue
            self.succeeded()
            return result
        raise AssertionError("unreachable")  # the last attempt returns or raises


class BackoffCancelled(Exception):
    """A model call was not made: the Backoff it would wait on was cancelled."""


class _Pause:
    """The pause state linked Backoffs share (Backoff.linked)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.resume_at = 0.0
        self.delay = 0.0  # the pause in force, or the last one
        self.streak = 0  # pauses since the last success
        self.limits = 0  # every rate limit seen


class BackedOffLlm:
    """Hermes' ctx.llm, with every complete_structured call made through `backoff`
    (Backoff.call): it waits out a pause another call started, and a rate-limited call is
    made again. evaluate hands it to the dimensions it scores while the task runs call the
    same provider (Safety's Cisco analyzer and review). Anything else is ctx.llm's own."""

    def __init__(self, llm: Any, backoff: Backoff):
        self._llm, self._backoff = llm, backoff

    def complete_structured(self, **kwargs: Any) -> Any:
        return self._backoff.call(lambda: self._llm.complete_structured(**kwargs))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._llm, name)
