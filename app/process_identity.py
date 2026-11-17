"""Process instance identity helpers used by the SQLite scan lease.

The identity combines a PID with the OS-recorded process creation time so PID
reuse cannot make a dead scanner look alive. Failure to inspect a process is
reported as unknown and is deliberately treated as a lock, not as stale.
"""

from __future__ import annotations

import os
import sys
from typing import Optional


def process_start_identity(pid: int) -> Optional[str]:
    """Return a stable start identity for a live process, or None if unknown."""
    if pid <= 0:
        return None
    if os.name == "nt":
        return _windows_process_start_identity(pid)
    if sys.platform.startswith("linux"):
        return _linux_process_start_identity(pid)
    return None


def process_instance_alive(pid: int, expected_identity: str) -> Optional[bool]:
    """Return True/False when verifiable, otherwise None (fail closed)."""
    if pid <= 0 or not expected_identity:
        return None
    if os.name == "nt":
        identity, error = _windows_process_identity_result(pid)
        if identity is not None:
            return identity == expected_identity
        # These Win32 errors mean the PID does not identify a process.
        if error in (6, 87, 1168):  # INVALID_HANDLE, INVALID_PARAMETER, NOT_FOUND
            return False
        return None
    if sys.platform.startswith("linux"):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return None
        identity = _linux_process_start_identity(pid)
        return None if identity is None else identity == expected_identity
    return None


def _linux_process_start_identity(pid: int) -> Optional[str]:
    try:
        # Field 22 is process start time in clock ticks since boot. The final
        # ')' is used because the command name in /proc/PID/stat can contain spaces.
        raw = open(f"/proc/{pid}/stat", encoding="ascii").read()
        tail = raw[raw.rfind(")") + 2 :].split()
        start_ticks = tail[19]
        boot_id = open("/proc/sys/kernel/random/boot_id", encoding="ascii").read().strip()
        return f"linux:{boot_id}:{start_ticks}"
    except (OSError, IndexError, ValueError):
        return None


def _windows_process_start_identity(pid: int) -> Optional[str]:
    identity, _error = _windows_process_identity_result(pid)
    return identity


def _windows_process_identity_result(pid: int) -> tuple[Optional[str], Optional[int]]:
    if os.name != "nt":
        return None, None
    import ctypes
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(FILETIME),
                                         ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
                                         ctypes.POINTER(FILETIME)]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None, ctypes.get_last_error()
    try:
        created, exited, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                        ctypes.byref(kernel), ctypes.byref(user)):
            return None, ctypes.get_last_error()
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return f"windows:{ticks}", None
    finally:
        kernel32.CloseHandle(handle)
