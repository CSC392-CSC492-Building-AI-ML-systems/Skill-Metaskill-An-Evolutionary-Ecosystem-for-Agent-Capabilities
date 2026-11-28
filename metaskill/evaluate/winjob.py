"""A Windows Job Object holding a process and everything it starts (hermes_agent.md 4).

Windows has no process-group signal. `taskkill /T` walks parent links, so it misses a process
whose parent has already exited; a job does not: every process started inside it stays in it,
wherever it sits in the tree. The job is made with KILL_ON_JOB_CLOSE, so closing it, or this
process dying, also kills what is left in it.

Standard library only (ctypes). Import it on Windows only.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9  # JOBOBJECTINFOCLASS
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_TH32CS_SNAPTHREAD = 0x00000004
_THREAD_SUSPEND_RESUME = 0x0002
_INVALID_HANDLE = ctypes.c_void_p(-1).value
CREATE_SUSPENDED = 0x00000004  # start the process with `creationflags` | this, then contain()


class _ThreadEntry(ctypes.Structure):  # THREADENTRY32
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
                ("dwFlags", wintypes.DWORD)]


class _BasicLimits(ctypes.Structure):  # JOBOBJECT_BASIC_LIMIT_INFORMATION
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _IoCounters(ctypes.Structure):  # IO_COUNTERS
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimits(ctypes.Structure):  # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    _fields_ = [("BasicLimitInformation", _BasicLimits),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _kernel32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    # Handles are pointer-sized: without these, ctypes would cut them to 32 bits.
    k.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                          wintypes.DWORD]
    k.SetInformationJobObject.restype = wintypes.BOOL
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.AssignProcessToJobObject.restype = wintypes.BOOL
    k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.TerminateJobObject.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]
    k.Thread32First.restype = wintypes.BOOL
    k.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]
    k.Thread32Next.restype = wintypes.BOOL
    k.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenThread.restype = wintypes.HANDLE
    k.ResumeThread.argtypes = [wintypes.HANDLE]
    k.ResumeThread.restype = wintypes.DWORD
    return k


class Job:
    """A kill-on-close job with one process assigned. `contain` makes one.

    Thread-safe: HermesAgent.cancel() kills a session's job from one thread while that
    session's own thread may be closing it. Without the lock, kill() could use the handle
    value just after close() freed it, when Windows may have given it to a new job (another
    session's) in the same process."""

    def __init__(self, kernel32, handle: int):
        self._k, self._handle = kernel32, handle
        self._lock = threading.Lock()

    def kill(self) -> bool:
        """Kill every process in the job. False if Windows refused, or it is closed."""
        with self._lock:
            return bool(self._handle) and bool(self._k.TerminateJobObject(self._handle, 1))

    def close(self) -> None:
        """Close the job, which kills whatever is still in it. Safe to call twice."""
        with self._lock:
            if self._handle:
                self._k.CloseHandle(self._handle)
                self._handle = None


def resume(pid: int) -> None:
    """Resume the threads of process `pid`, started with CREATE_SUSPENDED. Raises OSError if
    none could be resumed: the process would then never run, and the caller must kill it."""
    k = _kernel32()
    snapshot = k.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    if not snapshot or snapshot == _INVALID_HANDLE:
        raise ctypes.WinError(ctypes.get_last_error())
    resumed = 0
    try:
        entry = _ThreadEntry()
        entry.dwSize = ctypes.sizeof(entry)
        more = k.Thread32First(snapshot, ctypes.byref(entry))
        while more:
            if entry.th32OwnerProcessID == pid:
                thread = k.OpenThread(_THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                if thread:
                    if k.ResumeThread(thread) != 0xFFFFFFFF:
                        resumed += 1
                    k.CloseHandle(thread)
            more = k.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        k.CloseHandle(snapshot)
    if not resumed:
        raise OSError(f"could not resume process {pid}")


def contain(pid: int) -> Job:
    """A new job holding process `pid`. Raises OSError if Windows refuses (the caller falls
    back to taskkill). Processes `pid` starts after this are in the job too; ones it started
    before are not (a venv's python.exe starts the real one at once), so start it with
    CREATE_SUSPENDED, contain it, then resume() it."""
    k = _kernel32()
    job = k.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                         ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        process = k.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not k.AssignProcessToJobObject(job, process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            k.CloseHandle(process)
    except BaseException:
        k.CloseHandle(job)
        raise
    return Job(k, job)
