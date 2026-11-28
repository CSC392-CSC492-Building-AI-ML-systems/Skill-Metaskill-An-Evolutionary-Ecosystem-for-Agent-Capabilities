"""host.py: how many Hermes sessions the host's memory holds (runner.md 2.7)."""

import sys
import threading
import time
from types import SimpleNamespace

import pytest

from metaskill.evaluate import host
from metaskill.evaluate.host import available_memory as real_available_memory
from metaskill.evaluate.models import EvaluationConfig, Skill
from metaskill.evaluate.runner import AgentRun, run_tasks
from sandbox_helpers import FakeSandbox
from test_runner import FakeAgent, task
from test_evaluate_tasks import (  # noqa: F401  (harness is a fixture)
    CheckedAgent, cheap_with_skill, harness, make_skill)
from metaskill.evaluate import evaluate_skill

MB = 1024 * 1024


def test_the_measured_cost_is_300_mb_a_worker():
    # About 4800 MB for 16 workers (a running session and a warm child each).
    assert host.HERMES_MEMORY == 4800 * MB // 16 == 300 * MB


@pytest.mark.parametrize("available, room", [
    (4800 * MB, 16), (8 * 1024 * MB, 27), (299 * MB, 1), (1, 1), (None, None), (0, None)])
def test_capacity_is_available_memory_over_the_cost_of_a_worker(monkeypatch, available, room):
    monkeypatch.setattr(host, "available_memory", lambda: available)
    assert host.capacity() == room


def test_meminfo_available_is_read_in_bytes():
    text = "MemTotal:       16000000 kB\nMemFree:  100 kB\nMemAvailable:    4915200 kB\n"
    assert host._meminfo_available(text) == 4915200 * 1024
    assert host._meminfo_available("MemTotal: 1 kB\n") is None  # a kernel without it


@pytest.mark.skipif(not (sys.platform == "win32" or host.MEMINFO.is_file()),
                    reason="the host does not say how much memory is available")
def test_this_machine_says_how_much_memory_is_available():
    assert real_available_memory() > 0


def test_a_failed_reading_is_none(monkeypatch):
    monkeypatch.setattr(host, "_windows_available", lambda: None)
    monkeypatch.setattr(host, "MEMINFO", SimpleNamespace(is_file=lambda: True,
                                                         read_text=lambda **_: "garbage"))
    monkeypatch.setattr(host, "CGROUP", host.Path("/no/cgroup/here"))  # not the CI host's
    monkeypatch.setattr(host.sys, "platform", "linux")
    assert real_available_memory() is None


def _peak_runner(host_room, monkeypatch, tmp_path, measured=None):
    """Peak sessions at once and the prewarm asked for, with 8 allowed and 8 runs."""
    if measured is not None:
        monkeypatch.setattr(host, "capacity", lambda: measured)
    lock, running, peak = threading.Lock(), [0], [0]

    def slow(prompt, skill, workdir):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.05)
        with lock:
            running[0] -= 1
        return AgentRun(output="done")

    path = tmp_path / "s"
    path.mkdir()
    (path / "SKILL.md").write_text("# S\n", encoding="utf-8")
    agent = FakeAgent(slow)
    asked = []
    agent.prewarm = asked.append
    kwargs = {} if host_room == "left out" else {"host_room": host_room}
    run_tasks(Skill(path=path, name="s", body="# S\n"), [task("a"), task("b")], agent,
              config=EvaluationConfig(runs_per_task=2, max_parallel_runs=8),
              run_dir=tmp_path, sandbox=FakeSandbox(), **kwargs)
    return peak[0], asked


def test_the_hosts_memory_caps_how_many_run_at_once(monkeypatch, tmp_path):
    peak, asked = _peak_runner(2, monkeypatch, tmp_path)
    assert peak == 2 and asked == [2]


def test_an_unknown_host_memory_does_not_cap(monkeypatch, tmp_path):
    peak, asked = _peak_runner(None, monkeypatch, tmp_path)
    assert asked == [8] and peak > 2


def test_left_out_the_runner_reads_the_host_itself(monkeypatch, tmp_path):
    peak, asked = _peak_runner("left out", monkeypatch, tmp_path, measured=3)
    assert peak <= 3 and asked == [3]


def test_evaluate_reads_the_host_once_before_its_warm_children_start(monkeypatch, tmp_path,
                                                                  harness):
    """The warm children would count as used memory, so evaluate reads it before they
    start, and gives that one reading to its prewarm and to the runner."""
    events = []
    monkeypatch.setattr(host, "capacity", lambda: events.append("read") or 1)
    agent = CheckedAgent(cheap_with_skill)
    agent.prewarm = lambda n: events.append(("prewarm", n))
    harness.config.write_text('{"runs_per_task": 2, "max_parallel_runs": 8}')
    result = evaluate_skill(make_skill(tmp_path, "s"), config_path=harness.config,
                            output_path=tmp_path / "out", tasks_path=harness.tasks, agent=agent)
    assert result.get("status") != "error"
    # One reading, first; evaluate's prewarm and the runner's both held to it.
    assert events == ["read", ("prewarm", 1), ("prewarm", 1)]


def test_a_cgroup_limit_holds_the_reading_down(tmp_path):
    """In a container /proc/meminfo shows the host's memory; the cgroup's limit is what the
    sessions really have."""
    (tmp_path / "memory.max").write_text(f"{2048 * MB}\n")
    (tmp_path / "memory.current").write_text(f"{512 * MB}\n")
    assert host._cgroup_available(tmp_path) == 1536 * MB
    (tmp_path / "memory.max").write_text("max\n")
    assert host._cgroup_available(tmp_path) is None  # no limit
    assert host._cgroup_available(tmp_path / "missing") is None  # no cgroup v2


def test_on_linux_the_lower_of_meminfo_and_the_cgroup_counts(monkeypatch, tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(f"MemAvailable: {8 * 1024 * 1024} kB\n")  # 8 GB on the host
    (tmp_path / "memory.max").write_text(f"{1024 * MB}\n")
    (tmp_path / "memory.current").write_text("0\n")
    monkeypatch.setattr(host.sys, "platform", "linux")
    monkeypatch.setattr(host, "MEMINFO", meminfo)
    monkeypatch.setattr(host, "CGROUP", tmp_path)
    assert real_available_memory() == 1024 * MB
