import time
import threading
from types import SimpleNamespace

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication, QMenu

from codex_companion.app import Companion
from codex_companion.state import SuggestionState


class Popup:
    def __init__(self):
        self.visible = False
        self.shown = []

    def isVisible(self):
        return self.visible

    def hide(self):
        self.visible = False

    def show_text(self, text, bounds, *, suggest=False, **kwargs):
        self.visible = True
        self.shown.append(text)


def controller():
    app = QApplication.instance() or QApplication([])
    companion = Companion.__new__(Companion)
    QObject.__init__(companion)
    companion._test_app = app
    companion.app = app
    companion.config = SimpleNamespace(enabled=True)
    companion.monitor = SimpleNamespace(generation=1, wake=lambda: None, set_active=lambda _: None)
    companion.tailer = SimpleNamespace(revision=1, poll=lambda: False)
    companion.context_revision = 1
    from codex_companion.sessions import Message
    companion.context_messages = [Message("user", "Synthetic prior message")]
    companion.context_ready = True
    companion.context_verified = True
    companion.resolver = SimpleNamespace(generation=1, wake=lambda: None,
                                         invalidate=lambda: None)
    companion.refresh_menu = lambda: None
    companion.state = SuggestionState(debounce_seconds=0)
    companion.popup = Popup()
    companion.last_read = None
    companion.fallback_mode = True
    companion.text_armed = True
    companion.last_typing_at = 0.0
    companion.awaiting_committed_edit = False
    companion.request_started_at = time.monotonic()
    companion.draft_dirty = False
    companion.input_activity_pending = False
    companion.last_activity_kind = "keyboard"
    companion.mouse_down = False
    companion.awaiting_editor_click = False
    companion.pending_click_position = None
    companion.context_resolution_state = "waiting"
    companion.last_keyboard_log_at = 0.0
    companion.fallback_down = False
    companion.fallback_pending = False
    companion.ready = True
    companion._tray_status = "ready"
    companion._tray_icons = {}
    companion.inserting = False
    companion.cancel = None
    companion.pending_insertion = None
    companion.accept_hwnd = 123
    companion.last_codex_hwnd = 123
    companion.bounds = (100, 100, 200, 130)
    return companion


def test_hook_activity_invalidates_tab_before_queued_ui_update():
    companion = controller()
    companion.tab_hook = SimpleNamespace(ready=True)
    events = []
    companion.bridge = SimpleNamespace(
        key_activity=SimpleNamespace(emit=lambda: events.append("key")),
        mouse_activity=SimpleNamespace(emit=lambda x, y: events.append((x, y))))

    companion._hook_key_activity()
    assert companion.tab_hook.ready is False
    assert events == ["key"]

    companion.tab_hook.ready = True
    companion._hook_mouse_activity(10, 20)
    assert companion.tab_hook.ready is False
    assert events[-1] == (10, 20)


def test_tray_reports_actual_user_and_assistant_context_counts(monkeypatch):
    from codex_companion import i18n
    from codex_companion.sessions import Message

    # This test checks the Chinese menu layout, independent of the host locale.
    monkeypatch.setattr(i18n, "system_ui_languages", lambda: ["zh-CN"])
    companion = controller()
    companion.active_context_label = "当前任务" * 30
    companion.backend_error = ""
    companion.context_messages = [Message("user", "我的问题"),
                                  Message("assistant", "助手最终回复")]
    companion.menu = QMenu()
    tooltips = []
    companion.tray = SimpleNamespace(setToolTip=tooltips.append)
    companion.refresh_menu = Companion.refresh_menu.__get__(companion, Companion)

    companion.refresh_menu()

    assert any("用户 1 / 助手 1" in action.text() for action in companion.menu.actions())
    assert any("用户 1 / 助手 1" in text for text in tooltips)
    context = next(action.text() for action in companion.menu.actions()
                   if action.text().startswith("上下文："))
    assert context.startswith("上下文：当前任务当前任务")
    assert context.endswith("…")
    assert len(context) < 20
    assert companion.menu.sizeHint().width() < 320
    assert not any("选择 Codex 任务" in action.text() or "自动跟随" in action.text()
                   for action in companion.menu.actions())


def test_clearing_fallback_draft_cannot_resurrect_old_popup():
    companion = controller()
    companion.state.observe("旧草稿", 1, 0)
    requests = []
    companion.start_request = lambda: requests.append(companion.state.draft)
    companion.popup.show_text("旧建议", companion.bounds, suggest=True)

    companion.note_typing()  # User clears the draft with a real key.
    companion.tick()
    time.sleep(.31)  # The monitor reports after the keyboard quiet period.
    companion.on_observed(1, None, 0, companion.last_typing_at)  # UIA still cannot read it.
    companion.tick()

    assert not companion.popup.isVisible()
    assert requests == []


def test_ime_composition_hides_suggestion_and_reserves_tab(monkeypatch):
    from codex_companion import windows_input

    companion = controller()
    composing = [False]
    companion.ime_guard = SimpleNamespace(active_for=lambda _hwnd: composing[0])
    companion.ime_composing = False
    companion.state.observe("请帮我", 1, 0)
    token = companion.state.start()
    assert companion.state.finish(token, "检查配置")
    companion.popup.show_text("检查配置", companion.bounds, suggest=True)
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 123)
    assert companion.can_accept_tab()

    composing[0] = True
    assert not companion.can_accept_tab()
    companion.tick()
    assert not companion.popup.isVisible()
    assert companion.draft_dirty
    requests = []
    companion.start_request = lambda: requests.append(True)
    companion.tick()
    assert requests == []

    composing[0] = False
    companion.tick()
    assert not companion.popup.isVisible()


def test_unchanged_draft_after_ime_keys_does_not_reopen_popup(monkeypatch):
    from codex_companion import windows_input

    companion = controller()
    companion.fallback_mode = False
    companion.awaiting_committed_edit = False
    companion.ime_guard = SimpleNamespace(active_for=lambda _hwnd: False)
    companion.ime_composing = False
    companion.last_read = ("已输入", companion.bounds)
    companion.state.observe("已输入", 1, 0)
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 123)
    requests = []
    companion.start_request = lambda: requests.append(companion.state.draft)

    companion.note_typing()  # Pinyin keys are pressed; the editor still reports old text.
    companion.tick()
    companion.last_typing_at = time.monotonic() - 1
    companion.on_observed(1, ("已输入", companion.bounds), 123, companion.last_typing_at)
    companion.tick()

    assert companion.awaiting_committed_edit
    assert companion.draft_dirty
    assert requests == []
    assert not companion.popup.isVisible()

    companion.on_observed(1, ("已输入中文", companion.bounds), 123,
                          companion.last_typing_at)
    companion.tick()
    assert not companion.awaiting_committed_edit
    assert not companion.draft_dirty
    assert requests == ["已输入中文"]


def test_empty_editor_discards_late_suggestion(monkeypatch):
    from codex_companion import windows_input

    companion = controller()
    companion.fallback_mode = False
    companion.last_read = ("请帮我", companion.bounds)
    companion.state.observe("请帮我", 1, 0)
    token = companion.state.start()
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 123)
    companion.on_observed(1, ("", companion.bounds), 123, 0.0)
    monkeypatch.setattr(windows_input, "foreground_bounds", lambda: companion.bounds)

    companion.on_finished(token, "写一首诗")

    assert companion.state.draft == ""
    assert not companion.popup.isVisible()
    assert companion.popup.shown == []


def test_suggestion_needs_a_current_editor_snapshot(monkeypatch):
    from codex_companion import windows_input

    companion = controller()
    companion.fallback_mode = False
    companion.state.observe("旧草稿", 1, 0)
    token = companion.state.start()
    monkeypatch.setattr(windows_input, "foreground_bounds", lambda: companion.bounds)

    companion.on_finished(token, "旧建议")

    assert not companion.popup.isVisible()
    assert companion.popup.shown == []


def test_mouse_edit_invalidates_fallback_snapshot(monkeypatch):
    from codex_companion import windows_input

    companion = controller()
    companion.state.observe("旧草稿", 1, 0)
    companion.popup.show_text("旧建议", companion.bounds, suggest=True)
    monkeypatch.setattr(windows_input, "shortcut_pressed", lambda _: False)
    monkeypatch.setattr(windows_input.user32, "GetAsyncKeyState",
                        lambda vk: 0x8000 if vk == 0x01 else 0)

    companion.check_hotkeys()
    companion.tick()

    assert not companion.fallback_mode
    assert not companion.text_armed
    assert not companion.popup.isVisible()


def test_clicking_blank_editor_clears_old_result_and_cannot_rearm(monkeypatch):
    from codex_companion import windows_input

    companion = controller()
    companion.fallback_mode = False
    companion.last_read = ("旧草稿", companion.bounds)
    companion.state.observe("旧草稿", 1, 0)
    token = companion.state.start()
    companion.popup.show_text("旧建议", companion.bounds, suggest=True)
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 123)
    requests = []
    companion.start_request = lambda: requests.append(companion.state.draft)

    companion.note_mouse_activity()
    companion.tick()
    companion.on_finished(token, "过期建议")
    companion.on_observed(1, ("", companion.bounds), 123, companion.last_typing_at)
    companion.tick()

    assert companion.state.draft == ""
    assert not companion.text_armed
    assert not companion.popup.isVisible()
    assert requests == []


def test_click_does_not_request_even_if_uia_reports_stale_text(monkeypatch):
    from codex_companion import windows_input

    companion = controller()
    companion.fallback_mode = False
    companion.last_read = ("旧草稿", companion.bounds)
    companion.state.observe("旧草稿", 1, 0)
    token = companion.state.start()
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 123)
    requests = []
    companion.start_request = lambda: requests.append(companion.state.draft)

    companion.note_mouse_activity()
    companion.tick()
    companion.on_observed(1, ("旧草稿", companion.bounds), 123, companion.last_typing_at)
    companion.on_finished(token, "过期建议")
    companion.tick()

    assert requests == []
    assert not companion.popup.isVisible()


def test_completion_appears_only_in_the_editor_that_produced_it(monkeypatch):
    from codex_companion import windows_input

    companion = controller()
    companion.fallback_mode = False
    companion.last_read = ("请帮我", companion.bounds)
    companion.state.observe("请帮我", 1, 0)
    monkeypatch.setattr(windows_input, "foreground_bounds", lambda: companion.bounds)
    foreground = [999]
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: foreground[0])

    token = companion.state.start()
    companion.on_finished(token, "写一首诗")
    assert not companion.popup.isVisible()

    companion.state.observe("请帮我写", 1, 1)
    companion.last_read = ("请帮我写", companion.bounds)
    token = companion.state.start()
    foreground[0] = 123
    companion.on_finished(token, "诗")
    assert companion.popup.shown == ["诗"]


def test_tab_inserts_from_verified_snapshot_without_sync_uia(monkeypatch):
    from codex_companion import windows_input

    companion = controller()
    companion.fallback_mode = False
    companion.last_read = ("请帮我", companion.bounds)
    companion.state.observe("请帮我", 1, 0)
    token = companion.state.start()
    companion.state.finish(token, "写一首诗")
    companion.popup.show_text("写一首诗", companion.bounds, suggest=True)
    notices = []
    companion.tray = SimpleNamespace(showMessage=lambda *args: notices.append(args))
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(windows_input, "read_draft",
                        lambda: (_ for _ in ()).throw(AssertionError("synchronous UIA read")))
    monkeypatch.setattr(windows_input, "copy_draft_fallback",
                        lambda _: (_ for _ in ()).throw(AssertionError("synchronous clipboard read")))
    monkeypatch.setattr(windows_input, "foreground_bounds",
                        lambda: (_ for _ in ()).throw(AssertionError("process query")))
    inserted = []
    monkeypatch.setattr(windows_input, "insert_text",
                        lambda text, _clipboard, **kwargs: inserted.append((text, kwargs)) or True)

    companion.accept_suggestion()

    assert inserted == [("写一首诗", {"expected_hwnd": 123})]
    assert companion.state.draft == "请帮我写一首诗"
    assert not companion.popup.isVisible()
    assert companion.pending_insertion == ("请帮我", "写一首诗")
    companion.on_observed(1, ("请帮我写一首诗", companion.bounds),
                          123, companion.last_typing_at)
    assert companion.pending_insertion is None
    assert notices == []


def test_late_context_from_previous_task_is_ignored():
    companion = controller()
    previous = companion.context_messages[:]
    current = companion.tailer
    companion.on_context_changed(SimpleNamespace(), 9, ["旧任务"])
    assert companion.context_revision == 1
    assert companion.context_messages == previous

    companion.on_context_changed(current, 2, ["当前任务"])
    assert companion.context_revision == 2
    assert companion.context_messages == ["当前任务"]


def test_switch_cannot_send_previous_task_context(monkeypatch, tmp_path):
    from codex_companion import app as companion_app
    from codex_companion.sessions import Message

    companion = controller()
    old_tailer = companion.tailer
    companion.context_messages = [Message("assistant", "旧任务答复")]
    companion.state.observe("请继续", 1, 0)
    companion.backend = object()
    sent = []
    companion.inference = SimpleNamespace(
        submit=lambda token, request, backend: sent.append(request) or threading.Event())
    companion.session_poller = SimpleNamespace(set_tailer=lambda _: None)
    companion.monitor.set_active = lambda _: None
    monkeypatch.setattr(companion_app, "default_sessions_root", lambda: tmp_path)
    new_path = tmp_path / "rollout-new.jsonl"
    new_path.write_text('{"type":"session_meta","payload":{"id":"new"}}\n', encoding="utf-8")

    companion._bind_resolved_session(new_path)
    assert not companion.context_ready
    assert companion.context_messages == []
    companion.start_request()
    companion.on_context_changed(old_tailer, 9, [Message("assistant", "旧任务答复")])
    companion.start_request()
    assert sent == []

    companion.on_context_changed(companion.tailer, 1,
                                 [Message("assistant", "审核任务答复")])
    companion.start_request()
    assert sent == []

    companion.on_context_changed(companion.tailer, 2,
                                 [Message("user", "新任务问题"),
                                  Message("assistant", "新任务答复")])
    monkeypatch.setattr(companion_app.windows_input.user32, "GetForegroundWindow", lambda: 123)
    companion.on_observed(1, ("请继续", companion.bounds), 123, 0.0)
    companion.start_request()
    assert [message.text for message in sent[0].messages] == ["新任务问题", "新任务答复"]


def test_auto_mode_blocks_suggestions_until_visible_task_matches(monkeypatch):
    from codex_companion import windows_input

    companion = controller()
    companion.context_verified = True
    companion.last_read = ("旧草稿", companion.bounds)
    companion.state.observe("旧草稿", 1, 0)
    token = companion.state.start()
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 123)
    companion.refresh_menu = lambda: None

    companion.note_mouse_activity()
    assert not companion.context_verified
    companion.tick()
    companion.on_finished(token, "旧建议")
    companion.on_session_resolved(1, 123, None)
    assert not companion.popup.isVisible()


def test_auto_mode_scans_once_after_editor_click_not_each_key(monkeypatch):
    from codex_companion import windows_input

    companion = controller()
    companion.context_verified = True
    companion.context_resolution_state = "matched"
    companion.fallback_mode = False
    companion.last_read = ("旧草稿", companion.bounds)
    calls = []
    companion.resolver = SimpleNamespace(generation=1,
                                         invalidate=lambda: calls.append("invalidate"),
                                         wake=lambda: calls.append("wake"))
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 123)

    companion.note_mouse_activity(20, 20)  # Task/sidebar click.
    companion.tick()
    assert not companion.context_verified
    assert calls == ["invalidate"]

    companion.note_mouse_activity(150, 115)  # Return to the composer.
    companion.tick()
    assert calls == ["invalidate", "invalidate"]  # A coordinate is not focus proof.
    companion.on_observed(1, ("旧草稿", companion.bounds), 123,
                          companion.last_typing_at)
    assert calls == ["invalidate", "invalidate", "wake"]

    for _ in range(5):
        companion.note_typing()
        companion.tick()
    assert calls == ["invalidate", "invalidate", "wake"]


def test_clicking_another_app_over_the_old_editor_does_not_resolve(monkeypatch):
    from codex_companion import windows_input

    companion = controller()
    companion.context_verified = False
    calls = []
    companion.resolver = SimpleNamespace(generation=1,
                                         invalidate=lambda: calls.append("invalidate"),
                                         wake=lambda: calls.append("wake"))
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 999)

    companion.note_mouse_activity(150, 115)  # Other window covers old editor.
    companion.tick()
    companion.on_observed(1, None, 0, companion.last_typing_at)

    assert calls == ["invalidate"]
    assert not companion.awaiting_editor_click


def test_sidebar_click_does_not_resolve_if_editor_keeps_focus(monkeypatch):
    from codex_companion import windows_input

    companion = controller()
    calls = []
    companion.resolver = SimpleNamespace(generation=1,
                                         invalidate=lambda: calls.append("invalidate"),
                                         wake=lambda: calls.append("wake"))
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 123)

    companion.note_mouse_activity(20, 20)  # Sidebar, outside editor bounds.
    companion.tick()
    companion.on_observed(1, ("旧草稿", companion.bounds), 123,
                          companion.last_typing_at)

    assert calls == ["invalidate"]


def test_keyboard_navigation_waits_for_an_editor_click(monkeypatch):
    from codex_companion import windows_input

    companion = controller()
    calls = []
    companion.resolver = SimpleNamespace(generation=1,
                                         invalidate=lambda: calls.append("invalidate"),
                                         wake=lambda: calls.append("wake"))
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 123)

    companion.note_navigation()
    companion.tick()
    companion.on_observed(1, ("草稿", companion.bounds), 123,
                          companion.last_typing_at)

    assert calls == ["invalidate"]


def test_new_codex_window_resolves_only_after_editor_observation(monkeypatch):
    from codex_companion import windows_input

    companion = controller()
    companion.context_verified = True
    calls = []
    companion.resolver = SimpleNamespace(generation=1,
                                         invalidate=lambda: calls.append("invalidate"),
                                         wake=lambda: calls.append("wake"))
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 999)

    companion.note_mouse_activity(500, 500)  # Outside the old editor bounds.
    companion.tick()
    assert calls == ["invalidate"]
    companion.on_observed(1, ("新草稿", (400, 400, 700, 600)), 999,
                          companion.last_typing_at)

    assert calls == ["invalidate", "wake"]


def test_auto_mode_switches_to_uniquely_matched_task(monkeypatch, tmp_path):
    from codex_companion import windows_input

    companion = controller()
    companion.context_verified = False
    companion.tailer = SimpleNamespace(path=tmp_path / "old.jsonl")
    companion.refresh_menu = lambda: None
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 123)
    selected = []
    companion._bind_resolved_session = lambda path: selected.append(path) or True
    info = SimpleNamespace(path=tmp_path / "new.jsonl", preview="当前对话")

    companion.on_session_resolved(1, 123, info)

    assert selected == [info.path]
    assert companion.context_verified


def test_typing_recovers_unmatched_context_without_another_mouse_click(monkeypatch):
    from codex_companion import windows_input

    companion = controller()
    companion.tailer = None
    companion.context_verified = False
    companion.context_resolution_state = "unresolved"
    calls = []
    companion.resolver.wake = lambda: calls.append(True)
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 123)
    companion.note_typing()
    companion.on_observed(1, ("new draft", companion.bounds), 123, companion.last_typing_at)
    assert calls == [True]
    assert companion.state.draft == "new draft"
    assert companion.context_resolution_state == "checking"
    assert not companion.context_verified
    # Continued edits must not starve the in-flight resolver by restarting it.
    companion.note_typing()
    companion.on_observed(1, ("new draft two", companion.bounds), 123, companion.last_typing_at)
    assert calls == [True]


def test_cold_start_recognizes_focused_blank_composer_without_typing(monkeypatch):
    from codex_companion import windows_input

    companion = controller()
    companion.tailer = None
    companion.context_verified = False
    companion.text_armed = False
    calls = []
    companion.resolver.wake = lambda: calls.append(True)
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 123)
    companion.on_observed(1, ("", companion.bounds), 123, 0.0)
    assert calls == [True]
    assert companion.context_resolution_state == "checking"
    assert not companion.text_armed


def test_new_window_typing_cannot_reuse_previous_context(monkeypatch):
    from codex_companion import windows_input

    companion = controller()
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 999)
    companion.note_typing()
    companion.on_observed(1, ("another draft", companion.bounds), 999, companion.last_typing_at)
    assert not companion.context_verified
    assert companion.context_resolution_state == "checking"


def test_queued_key_signal_cannot_be_rearmed_by_timer(monkeypatch):
    from codex_companion import windows_input

    companion = controller()
    companion.tab_hook = SimpleNamespace(ready=True)
    companion.state.observe("draft", 1, 0)
    companion.state.finish(companion.state.start(), " suffix")
    companion.popup.show_text(" suffix", companion.bounds, suggest=True)
    companion.bridge = SimpleNamespace(key_activity=SimpleNamespace(emit=lambda: None))
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 123)
    companion._hook_key_activity()  # Qt has not yet received note_typing().
    companion.tick()
    assert not companion.tab_hook.ready


def test_popup_displays_the_entire_suffix_as_plain_text(monkeypatch):
    from PySide6.QtCore import Qt
    from codex_companion import app as companion_app

    companion = controller()
    popup = companion_app.SuggestionPopup()
    monkeypatch.setattr(companion_app, "popup_position", lambda *_, **kwargs: None)
    suffix = '<b>literal text</b> ' + 'long suffix ' * 6
    popup.show_text(suffix, companion.bounds, suggest=True)
    assert popup.label.text() == suffix
    assert popup.label.textFormat() == Qt.PlainText
    popup.close()
