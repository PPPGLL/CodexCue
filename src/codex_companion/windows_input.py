"""Small Windows-only boundary for reading and inserting composer text."""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from typing import Callable

import psutil


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
imm32 = ctypes.windll.imm32
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
user32.SetWindowPos.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.IsWindowVisible.argtypes = (wintypes.HWND,)


class _GUIThreadInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT)]


user32.GetGUIThreadInfo.argtypes = (wintypes.DWORD, ctypes.POINTER(_GUIThreadInfo))
user32.GetGUIThreadInfo.restype = wintypes.BOOL
imm32.ImmGetContext.argtypes = (wintypes.HWND,)
imm32.ImmGetContext.restype = wintypes.HANDLE
imm32.ImmReleaseContext.argtypes = (wintypes.HWND, wintypes.HANDLE)
imm32.ImmGetCompositionStringW.argtypes = (wintypes.HANDLE, wintypes.DWORD,
                                           ctypes.c_void_p, wintypes.DWORD)
imm32.ImmGetCompositionStringW.restype = ctypes.c_long


def _imm_has_composition() -> bool:
    """Check the focused control's IMM composition without reading its text."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    thread_id = user32.GetWindowThreadProcessId(hwnd, None)
    info = _GUIThreadInfo()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
        return False
    focus = info.hwndFocus or hwnd
    context = imm32.ImmGetContext(focus)
    if not context:
        return False
    try:
        return imm32.ImmGetCompositionStringW(context, 0x0008, None, 0) > 0
    finally:
        imm32.ImmReleaseContext(focus, context)


_WinEventProc = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD,
                                  wintypes.HWND, ctypes.c_long, ctypes.c_long,
                                  wintypes.DWORD, wintypes.DWORD)
user32.SetWinEventHook.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
                                   _WinEventProc, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.DWORD)
user32.SetWinEventHook.restype = wintypes.HANDLE
user32.UnhookWinEvent.argtypes = (wintypes.HANDLE,)


class ImeGuard:
    """Track candidate UI events; IMM polling covers IMEs without a candidate window."""

    def __init__(self) -> None:
        self.target_hwnd = 0
        self.candidate_hwnd = 0
        self.last_event_at = 0.0
        self._callback = _WinEventProc(self._on_event)
        self.handle = user32.SetWinEventHook(0x8027, 0x8029, None,
                                              self._callback, 0, 0, 0)

    def _on_event(self, _hook: int, event: int, hwnd: int, _object: int,
                  _child: int, _thread: int, _time: int) -> None:
        if event in (0x8027, 0x8029):  # EVENT_OBJECT_IME_SHOW / CHANGE
            self.target_hwnd = user32.GetForegroundWindow()
            self.candidate_hwnd = hwnd
            self.last_event_at = time.monotonic()
        elif event == 0x8028 and (not self.candidate_hwnd or hwnd == self.candidate_hwnd):
            self.target_hwnd = 0
            self.candidate_hwnd = 0

    def active_for(self, hwnd: int) -> bool:
        if not hwnd or user32.GetForegroundWindow() != hwnd:
            return False
        if _imm_has_composition():
            return True
        if self.target_hwnd != hwnd:
            return False
        # A missed hide event cannot suppress completion indefinitely.
        if time.monotonic() - self.last_event_at > 30:
            self.target_hwnd = 0
            return False
        if self.candidate_hwnd and not user32.IsWindowVisible(self.candidate_hwnd):
            self.target_hwnd = 0
            return False
        return True

    def close(self) -> None:
        if self.handle:
            user32.UnhookWinEvent(self.handle)
            self.handle = None


def _is_codex_desktop_process(name: str, executable: str) -> bool:
    name = name.lower()
    executable = executable.lower().replace("/", "\\")
    return ((name == "chatgpt.exe" and "\\windowsapps\\openai.codex_" in executable)
            or (name == "codex.exe" and "\\openai\\codex\\" in executable))


def _codex_foreground() -> bool:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        process = psutil.Process(pid.value)
        # Only the owner of the foreground window counts. A helper launched by
        # Codex can have codex.exe in its ancestor chain without being Codex UI.
        return _is_codex_desktop_process(process.name(), process.exe())
    except (psutil.Error, OSError):
        return False


def _vscode_foreground() -> bool:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        return psutil.Process(pid.value).name().lower() in {"code.exe", "code - insiders.exe"}
    except (psutil.Error, OSError):
        return False


def _supported_foreground() -> bool:
    return _codex_foreground() or _vscode_foreground()


def _vscode_thread_root(control: object) -> object | None:
    """Require the Codex WebView around a VS Code ProseMirror editor."""
    thread = None
    webview = False
    current = control
    for _ in range(28):
        if current is None:
            break
        cls = current.ClassName or ""
        if "thread-scroll-container" in cls:
            thread = current
        if cls == "webview ready":
            webview = True
        current = current.GetParentControl()
    return thread if webview else None


def _editor() -> object | None:
    import uiautomation as auto

    control = auto.GetFocusedControl()
    if not control or control.ControlTypeName not in {"EditControl", "DocumentControl"}:
        return None
    # Codex windows also expose other editable/document controls (including a
    # full-window document). Only the composer is a ProseMirror control.
    if "ProseMirror" not in (control.ClassName or ""):
        return None
    if _vscode_foreground() and (control.ControlTypeName != "EditControl"
                                 or _vscode_thread_root(control) is None):
        return None
    return control


def read_draft() -> tuple[str, tuple[int, int, int, int]] | None:
    """Read only the focused Codex editor; never scrape another app/window."""
    if not _supported_foreground():
        return None
    try:
        control = _editor()
        if control is None:
            return None
        rect = control.BoundingRectangle
        bounds = (rect.left, rect.top, rect.right, rect.bottom)
        if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            return None
        try:
            value = control.GetValuePattern().Value
        except Exception:
            value = control.GetTextPattern().DocumentRange.GetText(-1)
        if value is None:
            return None
        # Electron's empty ProseMirror editor exposes its placeholder as both
        # ValuePattern and TextPattern text. It is not part of the user draft.
        if ("ProseMirror" in control.ClassName and str(value).strip() == control.Name.strip()
                and any(child.ClassName == "placeholder" for child in control.GetChildren())):
            value = ""
        return str(value).rstrip("\r\n"), bounds
    except Exception:
        return None


def visible_conversation_texts() -> list[str]:
    """Read text near the focused composer without traversing other VS Code views."""
    if not _supported_foreground():
        return []
    try:
        import uiautomation as auto

        editor = _editor()
        if editor is None:
            return []
        target = editor.BoundingRectangle
        vscode = _vscode_foreground()
        window = (_vscode_thread_root(editor) if vscode else
                  auto.ControlFromHandle(user32.GetForegroundWindow()))
        if window is None:
            return []
        # Recent messages and queued drafts sit closest to the composer. Keep
        # enough results to reach the title while bounding the UIA traversal.
        pending = [(window, 0)]
        found: list[str] = []
        short_labels: list[str] = []
        visited = 0
        deadline = time.monotonic() + (.28 if vscode else .12)
        while (pending and visited < (360 if vscode else 240)
               and len(found) < (24 if vscode else 16) and time.monotonic() < deadline):
            control, depth = pending.pop()
            visited += 1
            rect = control.BoundingRectangle
            has_bounds = rect.right > rect.left and rect.bottom > rect.top
            if has_bounds and (rect.right < target.left - 12
                               or rect.left > target.right + 12
                               or rect.top >= target.top):
                continue
            if has_bounds and control.ControlTypeName in {"TextControl", "DocumentControl"}:
                name = control.Name.strip()
                if len(name) >= 8:
                    found.append(name[:1000])
                elif len(name) >= 2 and len(short_labels) < 16:
                    short_labels.append(name)  # Short task titles use exact index matching only.
            if depth < 14:
                pending.extend((child, depth + 1) for child in control.GetChildren())
        return found + short_labels
    except Exception:
        return []


def focused_codex_editor() -> bool:
    try:
        return _supported_foreground() and _editor() is not None
    except Exception:
        return False


def foreground_bounds() -> tuple[int, int, int, int] | None:
    if not _supported_foreground():
        return None
    rect = wintypes.RECT()
    if not user32.GetWindowRect(user32.GetForegroundWindow(), ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def cursor_position() -> tuple[int, int] | None:
    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return None
    return point.x, point.y


# Ignore our own clipboard shortcuts without ignoring input from IMEs, voice
# typing, accessibility software, or remote keyboards.
_OWN_INPUT_TAG = 0x43435545
user32.keybd_event.argtypes = (wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t)


def _key(vk: int, up: bool = False) -> None:
    user32.keybd_event(vk, 0, 2 if up else 0, _OWN_INPUT_TAG)


def copy_draft_fallback(clipboard) -> str | None:
    """Explicit hotkey fallback. Restores text clipboard and leaves caret at end."""
    if not focused_codex_editor():
        return None
    old = clipboard.text()
    try:
        clipboard.setText("")
        _key(0x11)  # Ctrl
        _key(0x41)  # A
        _key(0x41, True)
        _key(0x43)  # C
        _key(0x43, True)
        _key(0x11, True)
        time.sleep(0.08)
        value = clipboard.text()
        _key(0x27)  # Right collapses selection at end
        _key(0x27, True)
        return value.rstrip("\r\n")
    finally:
        clipboard.setText(old)


kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = (ctypes.c_void_p,)
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = (ctypes.c_void_p,)
kernel32.GlobalFree.argtypes = (ctypes.c_void_p,)
user32.SetClipboardData.argtypes = (wintypes.UINT, ctypes.c_void_p)
user32.SetClipboardData.restype = ctypes.c_void_p


def _set_unicode_clipboard(text: str) -> bool:
    raw = text.encode("utf-16-le") + b"\x00\x00"
    handle = kernel32.GlobalAlloc(0x0002, len(raw))  # GMEM_MOVEABLE
    if not handle:
        return False
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        return False
    ctypes.memmove(pointer, raw, len(raw))
    kernel32.GlobalUnlock(handle)
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(.02)
    else:
        kernel32.GlobalFree(handle)
        return False
    try:
        if not user32.EmptyClipboard() or not user32.SetClipboardData(13, handle):
            kernel32.GlobalFree(handle)
            return False
        return True
    finally:
        user32.CloseClipboard()


_pending_clipboard_restore = None


def insert_text(text: str, clipboard, *, allow_fallback: bool = False,
                expected_hwnd: int | None = None) -> bool:
    """Paste Unicode text immediately; restore the clipboard after the target reads it."""
    if not text:
        return False
    if expected_hwnd is not None:
        # The caller already verified this exact Codex editor and its draft.
        # A cheap HWND check avoids two synchronous UIA/process queries on Tab.
        if not expected_hwnd or user32.GetForegroundWindow() != expected_hwnd:
            return False
    elif not _supported_foreground() or (not allow_fallback and not focused_codex_editor()):
        return False
    from PySide6.QtCore import QMimeData, QTimer

    global _pending_clipboard_restore
    pending = _pending_clipboard_restore
    if pending is not None and pending[0] is clipboard and pending[1] == user32.GetClipboardSequenceNumber():
        saved = pending[2]  # Consecutive Tab presses must restore the original clipboard.
    else:
        previous = clipboard.mimeData()
        saved = QMimeData()
        if previous is not None:
            for fmt in previous.formats():
                saved.setData(fmt, previous.data(fmt))
    if not _set_unicode_clipboard(text):
        return False
    sequence = user32.GetClipboardSequenceNumber()
    _pending_clipboard_restore = (clipboard, sequence, saved)

    def restore() -> None:
        global _pending_clipboard_restore
        # Leave a clipboard change made by the user or another app untouched.
        if (_pending_clipboard_restore is not None and _pending_clipboard_restore[0] is clipboard
                and _pending_clipboard_restore[1] == sequence):
            if user32.GetClipboardSequenceNumber() == sequence:
                clipboard.setMimeData(saved)
            _pending_clipboard_restore = None

    try:
        # The accepted suggestion is always a suffix.
        _key(0x11)
        _key(0x23)  # End
        _key(0x23, True)
        _key(0x11, True)
        _key(0x11)
        _key(0x56)  # V
        _key(0x56, True)
        _key(0x11, True)
    except Exception:
        restore()
        return False
    QTimer.singleShot(250, restore)
    return True


def shortcut_pressed(key: int) -> bool:
    """Ctrl+Alt+key edge handling is done by the caller."""
    return all(user32.GetAsyncKeyState(vk) & 0x8000 for vk in (0x11, 0x12, key))


def modifiers_released() -> bool:
    return not any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in (0x11, 0x12))


class _KeyboardEvent(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


_HookProc = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, _HookProc, ctypes.c_void_p, wintypes.DWORD)
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = (ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = (ctypes.c_void_p,)
kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetModuleHandleW.restype = ctypes.c_void_p
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT)
user32.GetMessageW.restype = ctypes.c_int
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT,
                                      wintypes.WPARAM, wintypes.LPARAM)
user32.PostThreadMessageW.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class _LowLevelHook:
    """Pump a global hook off the Qt thread so Qt cannot stall system input."""

    def _install(self, kind: int, callback: _HookProc, error_message: str) -> None:
        self.handle = None
        self.thread_id = 0
        ready = threading.Event()
        failure: list[OSError] = []

        def run() -> None:
            self.thread_id = kernel32.GetCurrentThreadId()
            message = wintypes.MSG()
            user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
            self.handle = user32.SetWindowsHookExW(
                kind, callback, kernel32.GetModuleHandleW(None), 0)
            if not self.handle:
                failure.append(OSError(ctypes.get_last_error(), error_message))
                ready.set()
                return
            ready.set()
            try:
                while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                    user32.TranslateMessage(ctypes.byref(message))
                    user32.DispatchMessageW(ctypes.byref(message))
            finally:
                user32.UnhookWindowsHookEx(self.handle)
                self.handle = None

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        if not ready.wait(2):
            raise OSError(error_message + " timed out")
        if failure:
            raise failure[0]

    def close(self) -> None:
        if getattr(self, "thread_id", 0):
            user32.PostThreadMessageW(self.thread_id, 0x12, 0, 0)  # WM_QUIT
            self.thread_id = 0
        thread = getattr(self, "_thread", None)
        if thread and thread is not threading.current_thread():
            thread.join(timeout=0.2)


class TabHook(_LowLevelHook):
    """Consume Tab only while a suggestion belongs to the focused Codex window."""

    def __init__(self, should_accept: Callable[[], bool], accepted: Callable[[], None],
                 on_activity: Callable[[], None] | None = None,
                 on_focus_change: Callable[[], None] | None = None) -> None:
        self.should_accept = should_accept
        self.accepted = accepted
        self.on_activity = on_activity
        self.on_focus_change = on_focus_change
        self.pressed = False
        self.ready = False
        self._callback = _HookProc(self._on_key)
        self._install(13, self._callback, "Cannot install Tab keyboard hook")

    def _on_key(self, code: int, message: int, pointer: int) -> int:
        if code < 0:
            return user32.CallNextHookEx(self.handle, code, message, pointer)
        event = ctypes.cast(pointer, ctypes.POINTER(_KeyboardEvent)).contents
        if event.flags & 0x10 and event.dwExtraInfo == _OWN_INPUT_TAG:
            return user32.CallNextHookEx(self.handle, code, message, pointer)
        if (message in (0x100, 0x104)
                and getattr(self, "on_focus_change", None)
                and event.vkCode in (0x21, 0x22)
                and user32.GetAsyncKeyState(0x11) & 0x8000):
            self.ready = False
            self.on_focus_change()
        if (message in (0x100, 0x104)
                and _may_change_text(event.vkCode) and self.on_activity):
            if hasattr(self, "ready"):
                self.ready = False
            self.on_activity()
        if event.vkCode == 0x09:  # VK_TAB
            if message in (0x100, 0x104):  # WM_KEYDOWN / WM_SYSKEYDOWN
                if self.pressed:
                    return 1
                if not any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in (0x10, 0x11, 0x12)):
                    if self.should_accept():
                        self.pressed = True
                        if hasattr(self, "ready"):
                            self.ready = False
                        self.accepted()
                        return 1
                # Tab that was not accepted (including Shift+Tab) can move focus
                # within the same window. HWND equality no longer proves focus.
                if getattr(self, "on_focus_change", None):
                    self.ready = False
                    self.on_focus_change()
            elif message in (0x101, 0x105) and self.pressed:  # key up
                self.pressed = False
                return 1
        return user32.CallNextHookEx(self.handle, code, message, pointer)

def _may_change_text(vk: int) -> bool:
    if vk in {0x08, 0x0d, 0x20, 0x2e, 0xe5, 0xe7}:  # Enter, IME and Unicode VK_PACKET.
        return True
    if vk in {0x09, 0x1b} or 0x21 <= vk <= 0x28 or 0x70 <= vk <= 0x87:
        return False  # Tab, Escape, navigation and function keys
    if 0x30 <= vk <= 0x5a or 0x60 <= vk <= 0x6f or 0xba <= vk <= 0xe2:
        if user32.GetAsyncKeyState(0x11) & 0x8000:  # Ctrl shortcuts
            return vk in {0x56, 0x58, 0x59, 0x5a}  # Paste, cut, redo, undo
        return True
    return False


class _MouseEvent(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class MouseHook(_LowLevelHook):
    """Invalidate an editor snapshot on every mouse click, including short clicks."""

    def __init__(self, on_click: Callable[[int | None, int | None], None]) -> None:
        self.on_click = on_click
        self._callback = _HookProc(self._on_mouse)
        self._install(14, self._callback, "Cannot install mouse hook")

    def _on_mouse(self, code: int, message: int, pointer: int) -> int:
        if code >= 0 and message in {0x201, 0x204, 0x207, 0x20b}:
            if pointer:
                point = ctypes.cast(pointer, ctypes.POINTER(_MouseEvent)).contents.pt
                self.on_click(point.x, point.y)
            else:
                self.on_click(None, None)
        return user32.CallNextHookEx(self.handle, code, message, pointer)
