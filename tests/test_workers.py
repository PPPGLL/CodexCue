import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def test_fast_switch_back_emits_fresh_snapshot_even_with_unchanged_history():
    from PySide6.QtCore import Qt
    from codex_companion.app import Bridge, SessionPoller

    first_ready = threading.Event()
    second_poll = threading.Event()
    release = threading.Event()
    returned = threading.Event()
    snapshots = []

    class Tailer:
        revision = 1
        polls = 0

        def poll(self):
            self.polls += 1
            if self.polls == 2:
                second_poll.set()
                assert release.wait(2)
            return False

        def context(self):
            return ["unchanged history"]

    first, second = Tailer(), Tailer()
    bridge = Bridge()

    def observed(source, _revision, _messages, generation):
        snapshots.append((source, generation))
        (first_ready if generation == 1 else returned).set()

    bridge.context_changed.connect(observed, Qt.DirectConnection)
    poller = SessionPoller(bridge)
    try:
        poller.set_tailer(first)
        assert first_ready.wait(2)
        assert second_poll.wait(2)
        poller.set_tailer(second)
        poller.set_tailer(first)
        release.set()
        assert returned.wait(2)
        assert snapshots == [(first, 1), (first, 3)]
    finally:
        release.set()
        poller.stop()
        poller.thread.join(timeout=2)


def test_hook_signal_is_delivered_on_qt_thread():
    from PySide6.QtCore import QCoreApplication, QObject
    from codex_companion.app import Bridge

    app = QCoreApplication.instance() or QCoreApplication([])
    bridge = Bridge()
    calls = []
    main_thread = threading.get_ident()

    class Receiver(QObject):
        def on_key(self):
            calls.append(threading.get_ident())

    receiver = Receiver()
    bridge.key_activity.connect(receiver.on_key)
    worker = threading.Thread(target=bridge.key_activity.emit)
    worker.start()
    worker.join(timeout=1)
    assert calls == []
    app.processEvents()
    assert calls == [main_thread]


def test_draft_monitor_reads_off_ui_thread(monkeypatch):
    from codex_companion import windows_input
    from codex_companion.app import Bridge, DraftMonitor

    observed_thread = []
    read_started = threading.Event()
    last_key = [time.monotonic()]

    def read_draft():
        import uiautomation as auto
        assert auto.GetRootControl()
        observed_thread.append(threading.get_ident())
        read_started.set()
        return None

    monkeypatch.setattr(windows_input, "read_draft", read_draft)
    monitor = DraftMonitor(Bridge(), lambda: last_key[0])
    try:
        monitor.set_active(True)
        assert read_started.wait(2)
        assert observed_thread[0] != threading.get_ident()
        time.sleep(0.9)
        assert len(observed_thread) == 1  # Idle input does not repeat UIA reads.
    finally:
        monitor.stop()
        monitor.thread.join(timeout=2)


def test_draft_monitor_discards_read_started_before_a_key(monkeypatch):
    from PySide6.QtCore import Qt
    from codex_companion import windows_input
    from codex_companion.app import Bridge, DraftMonitor

    last_key = [0.0]
    started = threading.Event()
    release = threading.Event()
    fresh_observed = threading.Event()
    reads = []
    observations = []

    def read_draft():
        reads.append(True)
        if len(reads) == 1:
            started.set()
            assert release.wait(2)
            return "旧草稿", (0, 0, 100, 30)
        return "新草稿", (0, 0, 100, 30)

    bridge = Bridge()
    def on_observed(_generation, read, _hwnd, _typing_at):
        observations.append(read)
        fresh_observed.set()

    bridge.observed.connect(on_observed, Qt.DirectConnection)
    monkeypatch.setattr(windows_input, "read_draft", read_draft)
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 123)
    monitor = DraftMonitor(bridge, lambda: last_key[0])
    try:
        monitor.set_active(True)
        assert started.wait(2)
        last_key[0] = time.monotonic()
        monitor.wake()
        release.set()
        assert fresh_observed.wait(2)
        assert observations == [("新草稿", (0, 0, 100, 30))]
    finally:
        release.set()
        monitor.stop()
        monitor.thread.join(timeout=2)


def test_draft_monitor_recovers_a_late_uia_commit_without_another_key(monkeypatch):
    from PySide6.QtCore import Qt
    from codex_companion import windows_input
    from codex_companion.app import Bridge, DraftMonitor

    reads = []
    last_key = [0.0]
    initial = threading.Event()
    fresh = threading.Event()
    bridge = Bridge()
    bridge.observed.connect(lambda _g, read, _h, _t:
                            (fresh.set() if read[0] == "new" else initial.set()),
                            Qt.DirectConnection)
    def read():
        reads.append(True)
        return ("new" if len(reads) >= 3 else "old", (0, 0, 100, 30))
    monkeypatch.setattr(windows_input, "read_draft", read)
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 123)
    monitor = DraftMonitor(bridge, lambda: last_key[0])
    try:
        monitor.set_active(True)
        assert initial.wait(2)
        last_key[0] = time.monotonic() - 1
        monitor.wake()
        assert fresh.wait(2)
        time.sleep(.2)
        assert len(reads) == 3  # Stops reading again once the change is observed.
    finally:
        monitor.stop()
        monitor.thread.join(timeout=2)


def test_draft_monitor_discards_read_from_previous_window(monkeypatch):
    from PySide6.QtCore import Qt
    from codex_companion import windows_input
    from codex_companion.app import Bridge, DraftMonitor

    reads = []
    observed = []
    first_read = threading.Event()
    focus = [123]

    def read_draft():
        reads.append(True)
        if len(reads) == 1:
            focus[0] = 999  # Focus changed while UIA was reading the old draft.
            first_read.set()
            return "旧草稿", (0, 0, 100, 30)
        return None

    bridge = Bridge()
    bridge.observed.connect(lambda _g, read, _h, _t: observed.append(read), Qt.DirectConnection)
    monkeypatch.setattr(windows_input, "read_draft", read_draft)
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: focus[0])
    monitor = DraftMonitor(bridge, lambda: 0.0)
    try:
        monitor.set_active(True)
        assert first_read.wait(2)
        time.sleep(.1)
        assert ("旧草稿", (0, 0, 100, 30)) not in observed
    finally:
        monitor.stop()
        monitor.thread.join(timeout=2)


def test_inference_worker_replaces_queued_request():
    from codex_companion.app import Bridge, InferenceWorker
    from codex_companion.model import SuggestionRequest
    from codex_companion.state import RequestToken

    first_started = threading.Event()
    release_first = threading.Event()
    third_done = threading.Event()
    started = []

    class Backend:
        def suggest(self, request, emit, cancel):
            started.append(request.draft)
            if request.draft == "一":
                first_started.set()
                assert release_first.wait(2)
            if request.draft == "三":
                third_done.set()
            return "续写"

    worker = InferenceWorker(Bridge())
    backend = Backend()
    try:
        for generation, draft in enumerate(("一", "二", "三"), 1):
            worker.submit(RequestToken(generation, draft, 0),
                          SuggestionRequest([], draft), backend)
            if generation == 1:
                assert first_started.wait(2)
        release_first.set()
        assert third_done.wait(2)
        assert started == ["一", "三"]
    finally:
        release_first.set()
        worker.stop()
        worker.thread.join(timeout=2)


def test_session_poller_reads_context_off_ui_thread():
    from PySide6.QtCore import Qt
    from codex_companion.app import Bridge, SessionPoller

    done = threading.Event()
    poll_threads = []
    snapshots = []

    class Tailer:
        revision = 3

        def poll(self):
            poll_threads.append(threading.get_ident())
            return True

        def context(self, **_kwargs):
            return ["context"]

    tailer = Tailer()
    bridge = Bridge()
    bridge.context_changed.connect(
        lambda source, revision, messages, _generation: (snapshots.append((source, revision, messages)), done.set()),
        Qt.DirectConnection)
    poller = SessionPoller(bridge)
    try:
        poller.set_tailer(tailer)
        assert done.wait(2)
        assert poll_threads[0] != threading.get_ident()
        assert snapshots[0] == (tailer, 3, ["context"])
    finally:
        poller.stop()
        poller.thread.join(timeout=2)


def test_task_resolver_matches_visible_conversation_off_ui_thread(monkeypatch, tmp_path):
    from PySide6.QtCore import Qt
    from codex_companion import windows_input
    from codex_companion import app as companion_app
    from codex_companion.sessions import SessionInfo

    info = SessionInfo("current", tmp_path / "current.jsonl", "", "", "", "",
                       ("请修复示例悬浮窗遮挡输入区域的问题，并保持快捷键响应。",))
    threads = []
    finished = threading.Event()
    results = []
    monkeypatch.setattr(windows_input, "_codex_foreground", lambda: True)
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(windows_input, "visible_conversation_texts",
                        lambda: threads.append(threading.get_ident()) or
                                ["请修复示例悬浮窗遮挡输入区域的问题，并保持快捷键响应。"])
    monkeypatch.setattr(companion_app.SessionIndex, "list", lambda _: [info])
    bridge = companion_app.Bridge()
    bridge.session_resolved.connect(
        lambda _gen, _hwnd, match: (results.append(match), finished.set()), Qt.DirectConnection)
    resolver = companion_app.TaskResolver(bridge)
    try:
        resolver.wake()
        assert finished.wait(3)
        assert results == [info]
        assert threads[0] != threading.get_ident()
    finally:
        resolver.stop()
        resolver.thread.join(timeout=2)


def test_task_resolver_retries_a_transient_empty_uia_snapshot(monkeypatch, tmp_path):
    from PySide6.QtCore import Qt
    from codex_companion import windows_input
    from codex_companion import app as companion_app
    from codex_companion.sessions import SessionInfo

    title = "请修复示例悬浮窗遮挡输入区域的问题"
    info = SessionInfo("current", tmp_path / "current.jsonl", "", "", "", "",
                       opening_text=title + "，并保持快捷键响应。")
    reads = []
    done = threading.Event()
    results = []

    def visible():
        reads.append(True)
        return [] if len(reads) == 1 else [title]

    monkeypatch.setattr(windows_input, "_codex_foreground", lambda: True)
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(windows_input, "visible_conversation_texts", visible)
    monkeypatch.setattr(companion_app.SessionIndex, "list",
                        lambda self, force_refresh=False: [info] if force_refresh else [])
    bridge = companion_app.Bridge()
    bridge.session_resolved.connect(
        lambda _gen, _hwnd, match: (results.append(match), done.set()), Qt.DirectConnection)
    resolver = companion_app.TaskResolver(bridge)
    try:
        resolver.wake()
        assert done.wait(3)
        assert len(reads) == 2
        assert results == [info]
    finally:
        resolver.stop()
        resolver.thread.join(timeout=2)


def test_task_resolver_does_not_report_failure_without_visible_conversation(monkeypatch,
                                                                              tmp_path):
    from PySide6.QtCore import Qt
    from codex_companion import windows_input
    from codex_companion import app as companion_app

    reads = []
    results = []
    monkeypatch.setattr(windows_input, "_supported_foreground", lambda: True)
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(windows_input, "visible_conversation_texts",
                        lambda: reads.append(True) or [])
    monkeypatch.setattr(windows_input, "focused_codex_editor", lambda: False)
    monkeypatch.setattr(companion_app.SessionIndex, "list", lambda self: [])
    bridge = companion_app.Bridge()
    bridge.session_resolved.connect(lambda *_: results.append(True), Qt.DirectConnection)
    resolver = companion_app.TaskResolver(bridge)
    try:
        resolver.wake()
        deadline = time.monotonic() + 2
        while len(reads) < 2 and time.monotonic() < deadline:
            time.sleep(.01)
        assert len(reads) == 2
        assert results == []
    finally:
        resolver.stop()
        resolver.thread.join(timeout=2)
