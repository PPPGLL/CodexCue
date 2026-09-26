"""Keep a hidden launcher from making every Qt window invisible on Windows."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import subprocess
import sys


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE),
    ]


def hidden_startup() -> bool:
    info = _StartupInfo()
    info.cb = ctypes.sizeof(info)
    get_info = ctypes.WinDLL("kernel32").GetStartupInfoW
    get_info.argtypes = (ctypes.POINTER(_StartupInfo),)
    get_info.restype = None
    get_info(ctypes.byref(info))
    return bool(info.dwFlags & subprocess.STARTF_USESHOWWINDOW and info.wShowWindow == 0)


def command(arguments: list[str]) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *arguments]
    return [sys.executable, "-m", "codex_companion", *arguments]


def normalize_startup() -> int | None:
    """Relaunch once before Qt reads SW_HIDE; --background remains app-controlled.

    CREATE_NO_WINDOW suppresses a Python console without hiding GUI windows.
    An explicit SW_SHOWNORMAL prevents inheriting the original launcher's flag.
    Finite checks wait and propagate the real child's exit code to their runner.
    """
    if not hidden_startup():
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags = subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 1  # SW_SHOWNORMAL
    child = subprocess.Popen(
        command(sys.argv[1:]), startupinfo=info,
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    return child.wait() if any(arg in sys.argv for arg in ("--self-test", "--startup-test")) else 0
