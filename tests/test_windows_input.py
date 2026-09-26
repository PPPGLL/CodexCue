import pytest
import ctypes
import threading
from types import SimpleNamespace
from PySide6.QtCore import QCoreApplication, QMimeData, QTimer

pytestmark = pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows only")


def test_codexcue_settings_are_not_treated_as_codex_desktop():
    from codex_companion import windows_input as wi

    assert wi._is_codex_desktop_process(
        "ChatGPT.exe", r"C:\Program Files\WindowsApps\OpenAI.Codex_26.917\app\ChatGPT.exe")
    assert wi._is_codex_desktop_process(
        "codex.exe", r"C:\Users\user\AppData\Local\OpenAI\Codex\bin\codex.exe")
    assert not wi._is_codex_desktop_process(
        "CodexCue.exe", r"D:\PGL\对话补全\dist\CodexCue\CodexCue.exe")
    assert not wi._is_codex_desktop_process(
        "codex.exe", r"C:\Users\user\.vscode\extensions\openai.chatgpt\bin\codex.exe")


def test_foreground_does_not_inherit_codex_identity_from_parent(monkeypatch):
    from codex_companion import windows_input as wi

    def get_pid(_hwnd, pointer):
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_ulong)).contents.value = 42

    monkeypatch.setattr(wi, "user32", SimpleNamespace(
        GetForegroundWindow=lambda: 100, GetWindowThreadProcessId=get_pid))
    process = SimpleNamespace(
        name=lambda: "CodexCue.exe",
        exe=lambda: r"D:\PGL\对话补全\dist\CodexCue\CodexCue.exe",
        parents=lambda: [SimpleNamespace(
            name=lambda: "codex.exe",
            exe=lambda: r"C:\Users\user\AppData\Local\OpenAI\Codex\bin\codex.exe")],
    )
    monkeypatch.setattr(wi.psutil, "Process", lambda _pid: process)
    assert not wi._codex_foreground()


def test_empty_prosemirror_placeholder_is_not_a_draft(monkeypatch):
    from codex_companion import windows_input as wi

    rect = SimpleNamespace(left=1, top=2, right=101, bottom=52)
    children = [SimpleNamespace(ClassName="placeholder")]
    control = SimpleNamespace(
        BoundingRectangle=rect, Name="随心输入", ClassName="ProseMirror ProseMirror-focused",
        GetValuePattern=lambda: SimpleNamespace(Value="\n随心输入"),
        GetChildren=lambda: children,
    )
    monkeypatch.setattr(wi, "_codex_foreground", lambda: True)
    monkeypatch.setattr(wi, "_editor", lambda: control)

    assert wi.read_draft() == ("", (1, 2, 101, 52))
    children.clear()  # The user actually typed the same words as the placeholder.
    assert wi.read_draft() == ("\n随心输入", (1, 2, 101, 52))


def test_codex_document_control_is_not_the_composer(monkeypatch):
    from codex_companion import windows_input as wi
    import uiautomation as auto

    full_window = SimpleNamespace(ControlTypeName="DocumentControl",
                                  ClassName="webview ready")
    composer = SimpleNamespace(ControlTypeName="EditControl",
                               ClassName="ProseMirror ProseMirror-focused")
    monkeypatch.setattr(wi, "_codex_foreground", lambda: True)
    monkeypatch.setattr(wi, "_vscode_foreground", lambda: False)
    monkeypatch.setattr(auto, "GetFocusedControl", lambda: full_window)
    assert wi._editor() is None
    monkeypatch.setattr(auto, "GetFocusedControl", lambda: composer)
    assert wi._editor() is composer


def test_ime_guard_tracks_candidate_window_and_native_composition(monkeypatch):
    from codex_companion import windows_input as wi

    guard = object.__new__(wi.ImeGuard)
    guard.target_hwnd = 0
    guard.candidate_hwnd = 0
    guard.last_event_at = 0.0
    guard.handle = None
    monkeypatch.setattr(wi.user32, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(wi.user32, "IsWindowVisible", lambda hwnd: hwnd == 456)
    native = [False]
    monkeypatch.setattr(wi, "_imm_has_composition", lambda: native[0])

    guard._on_event(0, 0x8027, 456, 0, 0, 0, 0)  # Candidate shown.
    assert guard.active_for(123)
    assert not guard.active_for(999)
    guard._on_event(0, 0x8028, 456, 0, 0, 0, 0)  # Candidate hidden.
    assert not guard.active_for(123)
    native[0] = True
    assert guard.active_for(123)


def test_vscode_codex_editor_reads_draft_and_scans_only_its_webview(monkeypatch):
    from codex_companion import windows_input as wi
    import uiautomation as auto

    class Node:
        def __init__(self, kind, cls, name="", bounds=(500, 100, 900, 140), children=()):
            self.ControlTypeName = kind
            self.ClassName = cls
            self.Name = name
            self.BoundingRectangle = SimpleNamespace(**dict(zip(
                ("left", "top", "right", "bottom"), bounds)))
            self.children = list(children)
            self.parent = None
            for child in self.children:
                child.parent = self

        def GetChildren(self):
            return self.children

        def GetParentControl(self):
            return self.parent

        def GetValuePattern(self):
            return SimpleNamespace(Value="\nAsk Codex")

    placeholder = Node("GroupControl", "placeholder")
    editor = Node("EditControl", "ProseMirror ProseMirror-focused", "Ask Codex",
                  (500, 500, 900, 560), (placeholder,))
    message = Node("TextControl", "", "请只修改当前文件并解释原因", (500, 250, 900, 290))
    thread = Node("GroupControl", "thread-scroll-container", children=(message, editor))
    webview = Node("DocumentControl", "webview ready", children=(thread,))
    unrelated = Node("TextControl", "", "另一个视图的内容不应出现", (500, 220, 900, 260))
    Node("WindowControl", "Chrome_WidgetWin_1", children=(unrelated, webview))

    monkeypatch.setattr(wi, "_codex_foreground", lambda: False)
    monkeypatch.setattr(wi, "_vscode_foreground", lambda: True)
    monkeypatch.setattr(auto, "GetFocusedControl", lambda: editor)
    assert wi.read_draft() == ("", (500, 500, 900, 560))
    assert wi.visible_conversation_texts() == [message.Name]

    other_editor = Node("EditControl", "monaco-editor", "code", (500, 500, 900, 560))
    monkeypatch.setattr(auto, "GetFocusedControl", lambda: other_editor)
    assert wi.read_draft() is None
    assert wi.visible_conversation_texts() == []


def test_insert_pastes_without_enter(monkeypatch):
    from codex_companion import windows_input as wi

    class Clipboard:
        def __init__(self):
            self._data = QMimeData()
            self._data.setText("原剪贴板")
            self._data.setHtml("<b>原剪贴板</b>")

        def mimeData(self):
            return self._data

        def setText(self, text):
            self._data = QMimeData()
            self._data.setText(text)

        def text(self):
            return self._data.text()

        def setMimeData(self, data):
            self._data = data

    calls = []
    monkeypatch.setattr(wi, "_codex_foreground", lambda: True)
    monkeypatch.setattr(wi, "focused_codex_editor", lambda: True)
    monkeypatch.setattr(wi, "_key", lambda key, up=False: calls.append((key, up)))
    monkeypatch.setattr(wi, "_set_unicode_clipboard", lambda _: True)
    monkeypatch.setattr(wi.user32, "GetClipboardSequenceNumber", lambda: 1)
    app = QCoreApplication.instance() or QCoreApplication([])
    clipboard = Clipboard()
    assert wi.insert_text("中文😀", clipboard)
    assert all(key != 0x0D for key, _ in calls)  # no Enter
    assert (0x56, False) in calls  # paste
    QTimer.singleShot(300, app.quit)
    app.exec()
    assert clipboard.text() == "原剪贴板"
    assert clipboard.mimeData().html() == "<b>原剪贴板</b>"


def test_no_insertion_outside_codex(monkeypatch):
    from codex_companion import windows_input as wi

    monkeypatch.setattr(wi, "_codex_foreground", lambda: False)
    assert not wi.insert_text("不能发送", None)


def test_verified_window_fast_path_skips_uia(monkeypatch):
    from codex_companion import windows_input as wi

    monkeypatch.setattr(wi.user32, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(wi, "_codex_foreground",
                        lambda: (_ for _ in ()).throw(AssertionError("process query")))
    monkeypatch.setattr(wi, "focused_codex_editor",
                        lambda: (_ for _ in ()).throw(AssertionError("UIA query")))
    monkeypatch.setattr(wi, "_set_unicode_clipboard", lambda _: True)
    monkeypatch.setattr(wi, "_key", lambda *_: None)
    monkeypatch.setattr(wi.user32, "GetClipboardSequenceNumber", lambda: 1)
    QCoreApplication.instance() or QCoreApplication([])

    class Clipboard:
        def mimeData(self):
            return QMimeData()

        def setMimeData(self, _data):
            pass

    assert wi.insert_text("建议", Clipboard(), expected_hwnd=123)
    monkeypatch.setattr(wi.user32, "GetForegroundWindow", lambda: 999)
    assert not wi.insert_text("建议", Clipboard(), expected_hwnd=123)


def test_clipboard_change_during_paste_is_preserved(monkeypatch):
    from codex_companion import windows_input as wi

    class Clipboard:
        def __init__(self):
            self._data = QMimeData()
            self._data.setText("原内容")

        def mimeData(self):
            return self._data

        def setMimeData(self, data):
            self._data = data

        def setText(self, value):
            self._data = QMimeData()
            self._data.setText(value)

        def text(self):
            return self._data.text()

    app = QCoreApplication.instance() or QCoreApplication([])
    clipboard = Clipboard()
    sequence = [1]
    monkeypatch.setattr(wi, "_codex_foreground", lambda: True)
    monkeypatch.setattr(wi, "focused_codex_editor", lambda: True)
    monkeypatch.setattr(wi, "_key", lambda *_: None)
    monkeypatch.setattr(wi, "_set_unicode_clipboard", lambda _: True)
    monkeypatch.setattr(wi.user32, "GetClipboardSequenceNumber", lambda: sequence[0])
    assert wi.insert_text("建议", clipboard)
    clipboard.setText("用户新复制的内容")
    sequence[0] += 1
    QTimer.singleShot(300, app.quit)
    app.exec()
    assert clipboard.text() == "用户新复制的内容"


def test_tab_hook_consumes_only_active_suggestion(monkeypatch):
    from codex_companion import windows_input as wi

    active = [True]
    accepted = []
    hook = object.__new__(wi.TabHook)
    hook.should_accept = lambda: active[0]
    hook.accepted = lambda: accepted.append(True)
    hook.on_activity = None
    hook.pressed = False
    hook.handle = 1
    monkeypatch.setattr(wi.user32, "GetAsyncKeyState", lambda _: 0)
    monkeypatch.setattr(wi.user32, "CallNextHookEx", lambda *_: 0)
    tab = wi._KeyboardEvent()
    tab.vkCode = 0x09
    pointer = ctypes.addressof(tab)
    assert hook._on_key(0, 0x100, pointer) == 1
    assert hook._on_key(0, 0x100, pointer) == 1  # held Tab accepts once
    assert accepted == [True]
    assert hook._on_key(0, 0x101, pointer) == 1
    active[0] = False
    assert hook._on_key(0, 0x100, pointer) == 0
    assert accepted == [True]


def test_tab_hook_installs_off_the_caller_thread(monkeypatch):
    from codex_companion import windows_input as wi

    installed_on = []
    monkeypatch.setattr(wi.kernel32, "GetCurrentThreadId", lambda: 123)
    monkeypatch.setattr(wi.user32, "PeekMessageW", lambda *_: 0)
    monkeypatch.setattr(wi.user32, "GetMessageW", lambda *_: 0)
    monkeypatch.setattr(wi.user32, "UnhookWindowsHookEx", lambda *_: True)
    monkeypatch.setattr(wi.user32, "PostThreadMessageW", lambda *_: True)

    def install(*_args):
        installed_on.append(threading.get_ident())
        return 1

    monkeypatch.setattr(wi.user32, "SetWindowsHookExW", install)
    hook = wi.TabHook(lambda: False, lambda: None)
    try:
        assert installed_on and installed_on[0] != threading.get_ident()
    finally:
        hook.close()


def test_tab_hook_marks_real_typing_before_editor_detection(monkeypatch):
    from codex_companion import windows_input as wi

    activity = []
    hook = object.__new__(wi.TabHook)
    hook.should_accept = lambda: False
    hook.accepted = lambda: None
    hook.on_activity = lambda: activity.append(True)
    hook.pressed = False
    hook.handle = 1
    monkeypatch.setattr(wi.user32, "CallNextHookEx", lambda *_: 0)
    monkeypatch.setattr(wi.user32, "GetAsyncKeyState", lambda _: 0)
    key = wi._KeyboardEvent()
    key.vkCode = 0x41
    assert hook._on_key(0, 0x100, ctypes.addressof(key)) == 0
    key.vkCode = 0x10  # modifier keys do not change the draft
    hook._on_key(0, 0x100, ctypes.addressof(key))
    key.vkCode = 0x25  # navigation does not arm a blank editor
    hook._on_key(0, 0x100, ctypes.addressof(key))
    assert activity == [True]


@pytest.mark.parametrize("vk,flags", [(0x41, 0x10), (0xe7, 0x10), (0xe7, 0), (0xe5, 0)])
def test_external_input_reaches_draft_detection(monkeypatch, vk, flags):
    from codex_companion import windows_input as wi

    activity = []
    hook = object.__new__(wi.TabHook)
    hook.handle = 1
    hook.ready = True
    hook.on_activity = lambda: activity.append(True)
    hook.on_focus_change = None
    monkeypatch.setattr(wi.user32, "CallNextHookEx", lambda *_: 0)
    monkeypatch.setattr(wi.user32, "GetAsyncKeyState", lambda _: 0)
    event = wi._KeyboardEvent(vkCode=vk, flags=flags)
    pointer = ctypes.addressof(event)
    hook._on_key(0, 0x100, pointer)
    hook._on_key(0, 0x101, pointer)
    assert activity == [True]
    assert not hook.ready


@pytest.mark.parametrize("vk", [0x56, 0x43, 0x09, 0xe7])
def test_own_shortcuts_do_not_rearm_or_accept_completion(monkeypatch, vk):
    from codex_companion import windows_input as wi

    emitted = []
    monkeypatch.setattr(wi.user32, "keybd_event", lambda *args: emitted.append(args))
    wi._key(vk)
    wi._key(vk, True)
    hook = object.__new__(wi.TabHook)
    hook.handle = 1
    hook.ready = True
    hook.on_activity = lambda: pytest.fail("Own paste must not trigger another completion")
    hook.on_focus_change = lambda: pytest.fail("Own shortcut must not invalidate focus")
    hook.should_accept = lambda: pytest.fail("Own shortcut must not accept a suggestion")
    monkeypatch.setattr(wi.user32, "CallNextHookEx", lambda *_: 0)
    for key, _scan, flags, tag in emitted:
        assert tag != 0
        event = wi._KeyboardEvent(vkCode=key, flags=0x10, dwExtraInfo=tag)
        hook._on_key(0, 0x101 if flags & 2 else 0x100, ctypes.addressof(event))
    assert hook.ready


@pytest.mark.parametrize("vk", [0x09, 0x21, 0x22])
def test_external_injected_navigation_invalidates_focus(monkeypatch, vk):
    from codex_companion import windows_input as wi

    navigation = []
    hook = object.__new__(wi.TabHook)
    hook.handle = 1
    hook.ready = True
    hook.pressed = False
    hook.on_activity = lambda: pytest.fail("Navigation must not arm completion")
    hook.on_focus_change = lambda: navigation.append(True)
    hook.should_accept = lambda: pytest.fail("Ctrl navigation must not accept")
    monkeypatch.setattr(wi.user32, "CallNextHookEx", lambda *_: 0)
    monkeypatch.setattr(wi.user32, "GetAsyncKeyState", lambda key: 0x8000 if key == 0x11 else 0)
    event = wi._KeyboardEvent(vkCode=vk, flags=0x10)
    hook._on_key(0, 0x100, ctypes.addressof(event))
    assert navigation == [True]
    assert not hook.ready


def test_ctrl_tab_requests_context_switch_without_accepting_suggestion(monkeypatch):
    from codex_companion import windows_input as wi

    navigations = []
    accepted = []
    hook = object.__new__(wi.TabHook)
    hook.should_accept = lambda: True
    hook.accepted = lambda: accepted.append(True)
    hook.on_activity = None
    hook.on_focus_change = lambda: navigations.append(True)
    hook.pressed = False
    hook.handle = 1
    monkeypatch.setattr(wi.user32, "GetAsyncKeyState",
                        lambda vk: 0x8000 if vk == 0x11 else 0)
    monkeypatch.setattr(wi.user32, "CallNextHookEx", lambda *_: 0)
    tab = wi._KeyboardEvent()
    tab.vkCode = 0x09
    assert hook._on_key(0, 0x100, ctypes.addressof(tab)) == 0
    assert navigations == [True]
    assert accepted == []


@pytest.mark.parametrize("shift", [False, True])
def test_enter_and_shift_enter_invalidate_the_draft(monkeypatch, shift):
    from codex_companion import windows_input as wi

    activity = []
    hook = object.__new__(wi.TabHook)
    hook.ready = True
    hook.handle = 1
    hook.on_activity = lambda: activity.append(True)
    hook.on_focus_change = None
    monkeypatch.setattr(wi.user32, "CallNextHookEx", lambda *_: 0)
    monkeypatch.setattr(wi.user32, "GetAsyncKeyState",
                        lambda vk: 0x8000 if shift and vk == 0x10 else 0)
    event = wi._KeyboardEvent()
    event.vkCode = 0x0d
    hook._on_key(0, 0x100, ctypes.addressof(event))
    assert activity == [True]
    assert not hook.ready


def test_shift_tab_invalidates_focus_before_another_tab_can_be_accepted(monkeypatch):
    from codex_companion import windows_input as wi

    navigation = []
    hook = object.__new__(wi.TabHook)
    hook.handle = 1
    hook.ready = True
    hook.pressed = False
    hook.on_activity = None
    hook.on_focus_change = lambda: navigation.append(True)
    hook.should_accept = lambda: hook.ready
    hook.accepted = lambda: pytest.fail("Shift+Tab must preserve navigation")
    monkeypatch.setattr(wi.user32, "CallNextHookEx", lambda *_: 0)
    monkeypatch.setattr(wi.user32, "GetAsyncKeyState", lambda vk: 0x8000 if vk == 0x10 else 0)
    event = wi._KeyboardEvent()
    event.vkCode = 0x09
    assert hook._on_key(0, 0x100, ctypes.addressof(event)) == 0
    assert navigation == [True]
    assert not hook.ready


def test_mouse_hook_sees_a_short_click(monkeypatch):
    from codex_companion import windows_input as wi

    clicks = []
    hook = object.__new__(wi.MouseHook)
    hook.on_click = lambda x, y: clicks.append((x, y))
    hook.handle = 1
    monkeypatch.setattr(wi.user32, "CallNextHookEx", lambda *_: 0)
    event = wi._MouseEvent()
    event.pt.x, event.pt.y = 123, 456
    assert hook._on_mouse(0, 0x201, ctypes.addressof(event)) == 0
    assert hook._on_mouse(0, 0x202, 0) == 0
    assert clicks == [(123, 456)]


def test_visible_conversation_excludes_sidebar_and_draft(monkeypatch):
    from codex_companion import windows_input as wi
    import uiautomation as auto

    class Node:
        def __init__(self, kind, name, bounds, children=()):
            self.ControlTypeName = kind
            self.Name = name
            self.BoundingRectangle = type("Rect", (), dict(zip(
                ("left", "top", "right", "bottom"), bounds)))()
            self.children = children

        def GetChildren(self):
            return self.children

    sidebar = Node("TextControl", "另一个任务的侧边栏标题", (10, 100, 200, 130))
    message = Node("TextControl", "当前对话中的最后一条消息", (520, 700, 900, 750))
    below = Node("TextControl", "草稿下方的无关文字", (520, 1100, 900, 1150))
    editor = Node("EditControl", "", (500, 1000, 1000, 1080))
    window = Node("WindowControl", "Codex", (0, 0, 1400, 1200),
                  (sidebar, message, below, editor))
    monkeypatch.setattr(wi, "_codex_foreground", lambda: True)
    monkeypatch.setattr(wi, "_editor", lambda: editor)
    monkeypatch.setattr(wi.user32, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(auto, "ControlFromHandle", lambda _: window)

    assert wi.visible_conversation_texts() == ["当前对话中的最后一条消息"]


def test_visible_conversation_keeps_title_after_queued_items(monkeypatch):
    from codex_companion import windows_input as wi
    import uiautomation as auto

    class Node:
        def __init__(self, kind, name, top, children=()):
            self.ControlTypeName = kind
            self.Name = name
            self.ClassName = ""
            self.BoundingRectangle = SimpleNamespace(left=500, top=top, right=950,
                                                      bottom=top + 30)
            self.children = children

        def GetChildren(self):
            return self.children

    title = Node("TextControl", "当前对话的标题信息", 100)
    queued = [Node("TextControl", f"排队中的第 {index} 条消息", 700 + index)
              for index in range(10)]
    editor = Node("EditControl", "", 1000)
    window = Node("WindowControl", "Codex", 0, (title, *queued, editor))
    monkeypatch.setattr(wi, "_codex_foreground", lambda: True)
    monkeypatch.setattr(wi, "_editor", lambda: editor)
    monkeypatch.setattr(wi.user32, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(auto, "ControlFromHandle", lambda _: window)

    visible = wi.visible_conversation_texts()
    assert len(visible) == 11
    assert title.Name in visible
