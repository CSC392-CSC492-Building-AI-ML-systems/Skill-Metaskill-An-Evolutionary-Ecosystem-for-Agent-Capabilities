"""How many task sessions the host's memory has room for (runner.md 2.7).

Each session's Hermes process runs on the host, outside its container, so Docker's
capacity (DockerSandbox.capacity) does not count it. HERMES_MEMORY is what one worker
costs: measured as about 4800 MB for 16 workers, each a running Hermes session plus the
warm child waiting for the next one (prewarm_agents), on ab-test-readout (Windows 11,
Hermes 0.23). Without warm children a worker costs less, so the cap is then on the safe
side.

Reads the memory available now, with no extra dependency: Windows' GlobalMemoryStatusEx
and Linux's /proc/meminfo, held to the cgroup's own limit when there is one (in a
container, /proc/meminfo shows the host's memory). Elsewhere (macOS) it says nothing, and
only Docker caps the workers.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERMES_MEMORY = 300 * 1024 * 1024  # bytes per worker: 4800 MB / 16 measured
MEMINFO = Path("/proc/meminfo")
CGROUP = Path("/sys/fs/cgroup")  # cgroup v2: memory.max and memory.current


def available_memory() -> int | None:
    """Bytes of memory the host has available now, or None if it does not say."""
    try:
        if sys.platform == "win32":
            return _windows_available()
        if MEMINFO.is_file():
            available = _meminfo_available(MEMINFO.read_text(encoding="ascii",
                                                             errors="replace"))
            limit = _cgroup_available(CGROUP)
            if available is None or limit is None:
                return available if limit is None else limit
            return min(available, limit)
    except (OSError, ValueError, AttributeError):
        pass
    return None


def capacity(per_worker: int = HERMES_MEMORY) -> int | None:
    """How many workers the available memory holds at `per_worker` bytes each, at least 1;
    None if the host does not say. Read it before any warm child starts, since it counts
    the memory those children take as used."""
    available = available_memory()
    if available is None or available < 1 or per_worker < 1:
        return None
    return max(1, available // per_worker)


def _meminfo_available(text: str) -> int | None:
    """MemAvailable from /proc/meminfo, in bytes; None on kernels too old to give it."""
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        if name == "MemAvailable":
            value, *unit = rest.split()
            return int(value) * (1024 if unit == ["kB"] else 1)
    return None


def _cgroup_available(root: Path) -> int | None:
    """What the cgroup's memory limit leaves (memory.max - memory.current), or None
    without a limit ("max") or a cgroup v2 to read."""
    try:
        limit = (root / "memory.max").read_text(encoding="ascii").strip()
        if limit == "max":
            return None
        used = int((root / "memory.current").read_text(encoding="ascii").strip())
        return max(0, int(limit) - used)
    except (OSError, ValueError):
        return None


def _windows_available() -> int | None:
    import ctypes
    from ctypes import wintypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return int(status.ullAvailPhys)
