"""Isolated Windows integration acceptance, runnable from source or the release exe.

Uses real UI Automation, worker threads, local HTTP streaming, popup layout and
clipboard insertion. The host identity and input events are synthetic and are
reported as such; this is not a claim of Codex-host compatibility or real IME QA.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QVBoxLayout, QWidget

from .app import Companion
from .config import AppConfig, DEFAULT_OLLAMA_URL
from .diagnostics import setup_logging, shutdown_logging
from .i18n import tr
from . import windows_input as wi

TITLE_A = "补全功能的识别验证"  # Nine-character title, no visible message body.
TITLE_B = "另一个测试任务"
SUFFIX = " the current settings."
NEXT_SUFFIX = " Check the failure path too."
DETAIL_DRAFT = "页面的行间距看起来不一致，调整一下"
DETAIL_ITEMS = ["检查页面标题与正文各自的行间距是否一致，明确哪些位置存在过密或过疏的问题，便于逐项调整。",
                "比较相邻段落的留白关系，让同类内容保持清晰的阅读节奏，同时保留现有文字和布局顺序。",
                "检查长文本换行和窗口缩小时的显示情况，确保文字不重叠、不被截断，阅读顺序仍然清楚。"]


def write_fixture(home: Path, identity: str, title: str) -> None:
    sessions = home / "sessions" / "2026" / "09" / "25"
    sessions.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "session_meta", "payload": {"id": identity, "timestamp": "2026-09-25T00:00:00Z"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": f"Synthetic initial request for {identity}; unrelated to its title."}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant",
            "phase": "final_answer", "content": [{"type": "output_text", "text": f"Synthetic reply for {identity}."}]}},
    ]
    (sessions / f"rollout-{identity}.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    with (home / "session_index.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"id": identity, "thread_name": title,
                                 "updated_at": "2026-09-25T00:00:00Z"}, ensure_ascii=False) + "\n")


class ModelServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), ModelHandler)
        self.requests = []
        self.payloads = []
        self.release = threading.Event()
        self.release.set()


class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        body = json.dumps({"models": [{"name": "qa-fixture:1", "size": 1}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.payloads.append(payload)
        if payload.get("stream"):
            self.server.requests.append(json.loads(payload["messages"][-1]["content"]))
            self.server.release.wait(5)
        data = json.loads(payload["messages"][-1]["content"]) if payload.get("messages") else {"anchor": ""}
        suffix = "。" + "".join(DETAIL_ITEMS) if data.get("draft") == DETAIL_DRAFT else SUFFIX
        if data.get("draft") == "Please inspect" + SUFFIX:
            suffix = NEXT_SUFFIX
        result = {"continuation": data["anchor"] + suffix}
        body = json.dumps({"message": {"content": json.dumps(result)}, "done": True}).encode() + b"\n"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class FixtureWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CodexCue automated acceptance - synthetic data only")
        self.resize(720, 280)
        layout = QVBoxLayout(self)
        self.title = QLabel(TITLE_A)
        layout.addWidget(self.title)
        layout.addStretch()
        self.editor = QLineEdit()
        self.editor.setAccessibleName("CodexCue synthetic composer")
        layout.addWidget(self.editor)
        self.submissions = 0
        self.editor.returnPressed.connect(self._submit)

    def _submit(self):
        self.submissions += 1
        self.editor.clear()


def wait_for(app: QApplication, predicate, label: str, timeout=5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        loop = QEventLoop()
        QTimer.singleShot(10, loop.quit)
        loop.exec()  # Keep the native UIA provider responsive while workers query it.
    raise AssertionError(f"Timed out: {label}")


def send_unicode(text: str, target: int) -> None:
    """Exercise the installed Windows hook, including VK_PACKET input."""
    class KeyboardInput(ctypes.Structure):
        _fields_ = [("vk", wi.wintypes.WORD), ("scan", wi.wintypes.WORD),
                    ("flags", wi.wintypes.DWORD), ("time", wi.wintypes.DWORD),
                    ("extra", ctypes.c_size_t)]

    class MouseInput(ctypes.Structure):
        _fields_ = [("x", wi.wintypes.LONG), ("y", wi.wintypes.LONG),
                    ("data", wi.wintypes.DWORD), ("flags", wi.wintypes.DWORD),
                    ("time", wi.wintypes.DWORD), ("extra", ctypes.c_size_t)]

    class InputData(ctypes.Union):
        _fields_ = [("keyboard", KeyboardInput), ("mouse", MouseInput)]

    class Input(ctypes.Structure):
        _fields_ = [("kind", wi.wintypes.DWORD), ("data", InputData)]

    raw = text.encode("utf-16-le")
    units = [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]
    events = (Input * (2 * len(units)))()
    for index, unit in enumerate(units):
        for up in (0, 1):
            event = events[2 * index + up]
            event.kind = 1  # INPUT_KEYBOARD
            event.data.keyboard.scan = unit
            event.data.keyboard.flags = 4 | (2 if up else 0)  # UNICODE / KEYUP
    if wi.user32.GetForegroundWindow() != target or not wi.modifiers_released():
        raise AssertionError("Synthetic input requires the focused fixture and released modifiers")
    wi.user32.SendInput.argtypes = (wi.wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int)
    wi.user32.SendInput.restype = wi.wintypes.UINT
    if wi.user32.SendInput(len(events), events, ctypes.sizeof(Input)) != len(events):
        raise AssertionError("Windows did not accept all synthetic input events")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live-model", help="Use an already installed Ollama model for synthetic UI latency/quality trials")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    home = output / "synthetic-codex-home"
    home.mkdir(exist_ok=False)  # Never reuse or erase existing session data.
    write_fixture(home, "alpha", TITLE_A)
    write_fixture(home, "beta", TITLE_B)
    report = {"schema_version": 1, "status": "FAIL", "synthetic_only": True,
              "started_at": datetime.now(timezone.utc).isoformat(),
              "packaged": bool(getattr(sys, "frozen", False)),
              "executable_sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
              "scope": {"real_uia": True, "real_http": True, "real_clipboard_paste": True,
                        "real_codex_host": False, "physical_keyboard": False, "real_ime": False,
                        "real_keyboard_hook": True, "native_unicode_input": True,
                        "real_model": bool(args.live_model), "fallback_copy": False},
              "checks": [], "errors": []}
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = FixtureWindow()
    window.show()
    window.raise_()
    window.activateWindow()
    window.editor.setFocus()
    target = int(window.winId())
    server = ModelServer()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    companion = None
    setup_logging(output / "acceptance.log")

    def fixture_editor():
        import uiautomation as auto
        if wi.user32.GetForegroundWindow() != target:
            return None
        control = auto.GetFocusedControl()
        if (control is not None and control.ProcessId == os.getpid()
                and control.Name == "CodexCue synthetic composer"):
            return control
        return None

    def check(name, assertion):
        if not assertion:
            raise AssertionError(name)
        report["checks"].append({"name": name, "status": "PASS"})

    def key(vk):
        # Exercise the production low-level hook decision, but do not synthesize
        # global keystrokes into another app. Insertion is the production paste.
        event = wi._KeyboardEvent()
        event.vkCode = vk
        pointer = ctypes.addressof(event)
        down = companion.tab_hook._on_key(0, 0x100, pointer)
        companion.tab_hook._on_key(0, 0x101, pointer)
        return down

    def type_draft(text):
        check("fixture_has_focus", wi.user32.GetForegroundWindow() == target)
        window.editor.selectAll()
        send_unicode(text, target)
        wait_for(app, lambda: window.editor.text() == text, "native Unicode input readback")
        wait_for(app, lambda: companion.state.draft == text and not companion.draft_dirty,
                 "draft observed through real UIA")

    try:
        # Identity overrides exist only in this isolated QA process; production
        # still accepts only the supported Codex composer/process identities.
        with patch.dict(os.environ, {"CODEX_HOME": str(home), "LOCALAPPDATA": str(output / "appdata")}), \
                patch.object(wi, "_supported_foreground", lambda: wi.user32.GetForegroundWindow() == target), \
                patch.object(wi, "_vscode_foreground", lambda: False), \
                patch.object(wi, "_editor", fixture_editor):
            wait_for(app, lambda: wi.user32.GetForegroundWindow() == target,
                     "isolated fixture foreground (no input was sent elsewhere)", timeout=15)
            companion = Companion(app, AppConfig(
                ollama_url=args.ollama_url if args.live_model else f"http://127.0.0.1:{server.server_port}",
                ollama_model=args.live_model or "qa-fixture:1"))
            wait_for(app, lambda: companion.ready, "model ready", timeout=90 if args.live_model else 5)
            wait_for(app, lambda: companion.context_verified, "cold start focused composer recognition")
            check("cold_start_blank_no_request", not server.requests and not companion.popup.isVisible())
            check("cold_start_session_alpha", companion.tailer.path.name == "rollout-alpha.jsonl")

            if args.live_model:
                report["model"] = args.live_model
                report["semantic_review"] = "NOT_REVIEWED"
                report["model_check_scope"] = "Mechanical output and desktop insertion only; review meaning separately."
                report["model_results"] = []
                drafts = ["先不要修改文件，请先", "Please inspect the configu", "这个函数的返回值应该",
                          "帮我比较这两个实现的", "Before running the tests, please", "这个问题解决了吗？",
                          "我觉得", "你好"]
                rough_drafts = {"这个表格的展示方式不太好", "导出的时候总是不知道有没有成功，改一下",
                                   "先不要动代码，帮我看看这个登录流程有什么问题"}
                drafts.extend(rough_drafts)
                for draft in drafts:
                    began = time.monotonic()
                    type_draft(draft)
                    wait_for(app, lambda: companion.state.started_generation == companion.state.generation
                             and companion.state.active is None, "real model completed", timeout=10)
                    suffix = companion.state.suggestion
                    row = {"draft": draft, "suffix": suffix, "combined": draft + suffix,
                           "end_to_end_ms": round((time.monotonic() - began) * 1000),
                           "shown": companion.popup.isVisible()}
                    report["model_results"].append(row)
                    if suffix:
                        check("live_model_suffix_visible", bool(suffix) and companion.can_accept_tab())
                        if draft in rough_drafts:
                            row["suffix_chars"] = len(suffix)
                            check("rough_request_stays_concise", len(suffix) <= 80)
                        key(0x09)
                        wait_for(app, lambda: window.editor.text() == draft + suffix, "live suffix native paste")
                        check("live_model_paste_does_not_send", window.submissions == 0)
                        QTest.qWait(300)  # Let the production clipboard restoration finish.
                    else:
                        check("live_model_suffix_visible", False)
                # Mechanical success does not establish semantic understanding.
                from .quality_checks import check_output_contract
                quality_results = []
                quality_errors = []
                quality_done = threading.Event()
                def check_quality():
                    try:
                        quality_results.extend(check_output_contract(companion.backend))
                    except Exception as exc:
                        quality_errors.append(type(exc).__name__)
                    finally:
                        quality_done.set()
                threading.Thread(target=check_quality, daemon=True).start()
                wait_for(app, quality_done.is_set, "quoted-rule model regressions", timeout=40)
                report["output_contract_results"] = quality_results
                check("synthetic_output_contract", not quality_errors and len(quality_results) == 13
                      and all(row["status"] == "PASS" for row in quality_results))

                # Test residency beyond the former 60-second timeout, including
                # pause, the actual tray action, and reloading on the next edit.
                import httpx
                def resident():
                    response = httpx.get(args.ollama_url + "/api/ps", timeout=2, trust_env=False)
                    response.raise_for_status()
                    return any(m.get("name") == args.live_model for m in response.json().get("models", []))
                check("live_model_resident_before_idle", resident())
                companion.config.save = lambda: None
                companion.toggle()
                QTest.qWait(65000)
                check("live_model_stays_resident_while_idle_and_paused", resident())
                next(a for a in companion.menu.actions() if a.text() == tr("release_model")).trigger()
                wait_for(app, lambda: companion.releasing_backend is None, "tray model release")
                check("live_model_release_succeeded", companion.model_released and not companion.backend_error)
                check("live_model_explicit_release", not resident())
                companion.toggle()
                QTest.qWait(300)
                check("live_model_waits_for_next_edit", not resident())
                # The residency check deliberately idles for over a minute.
                # Restore only our synthetic fixture before the final input;
                # keep the foreground guard so no text reaches another app.
                window.raise_()
                window.activateWindow()
                window.editor.setFocus()
                wi.user32.SetForegroundWindow(target)
                wait_for(app, lambda: wi.user32.GetForegroundWindow() == target,
                         "fixture focus after idle (no input was sent elsewhere)", timeout=15)
                type_draft("Please inspect the configu")
                wait_for(app, lambda: companion.can_accept_tab(), "completion after manual release", timeout=90)
                check("live_model_reloads_on_next_edit", resident() and companion.ready)
                report["status"] = "PASS"
                return 0

            type_draft("Please inspect")
            wait_for(app, lambda: companion.context_verified, "nine-character title resolution")
            check("short_title_session_alpha", companion.tailer.path.name == "rollout-alpha.jsonl")
            wait_for(app, lambda: companion.can_accept_tab(), "visible suggestion")
            check("popup_matches_suffix", companion.popup.label.text() == SUFFIX)
            check("popup_native_visible", bool(wi.user32.IsWindowVisible(int(companion.popup.winId()))))
            clipboard_before = app.clipboard().text()
            check("tab_consumed", key(0x09) == 1)
            wait_for(app, lambda: window.editor.text() == "Please inspect" + SUFFIX, "native paste readback")
            check("native_paste_readback", True)
            wait_for(app, lambda: app.clipboard().text() == clipboard_before, "clipboard restoration")
            check("clipboard_restored", True)
            check("tab_does_not_send", window.submissions == 0)
            wait_for(app, lambda: companion.can_accept_tab(), "next suggestion after accepted paste")
            check("next_request_uses_accepted_text", server.requests[-1]["draft"] == "Please inspect" + SUFFIX)
            check("next_popup_matches_suffix", companion.state.suggestion == NEXT_SUFFIX)
            # Reproduce a clipboard reader that needs the Qt loop to progress.
            # Hold only the lock; never replace the user's clipboard contents.
            clipboard_locked = threading.Event()
            release_clipboard = threading.Event()
            def clipboard_reader():
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    if wi.user32.OpenClipboard(ctypes.c_void_p(target)):
                        try:
                            clipboard_locked.set()
                            release_clipboard.wait(2)
                        finally:
                            wi.user32.CloseClipboard()
                        return
                    time.sleep(.01)
            reader = threading.Thread(target=clipboard_reader, daemon=True)
            reader.start()
            busy_attempts = []
            set_clipboard = wi._set_unicode_clipboard
            def observed_clipboard_write(text):
                try:
                    return set_clipboard(text)
                except wi.ClipboardBusyError:
                    busy_attempts.append(True)
                    raise
            try:
                wait_for(app, clipboard_locked.is_set, "external clipboard reader")
                with patch.object(wi, "_set_unicode_clipboard", observed_clipboard_write):
                    check("second_tab_consumed", key(0x09) == 1)
                    QTimer.singleShot(60, release_clipboard.set)
                    wait_for(app, lambda: window.editor.text() == "Please inspect" + SUFFIX + NEXT_SUFFIX,
                             "consecutive Tab paste readback")
                check("busy_clipboard_retried", bool(busy_attempts))
            finally:
                release_clipboard.set()
                reader.join(timeout=2)
            QTest.qWait(300)
            check("only_tab_inserts_next_suggestion", window.editor.text() == "Please inspect" + SUFFIX + NEXT_SUFFIX)
            check("consecutive_tabs_preserve_clipboard", app.clipboard().text() == clipboard_before)
            check("consecutive_tabs_do_not_send", window.submissions == 0)

            # Enter invalidates a pending request, then the synthetic host clears
            # its own editor. A late response must not revive the old suggestion.
            server.release.clear()
            request_count = len(server.requests)
            type_draft("Please inspect again")
            wait_for(app, lambda: len(server.requests) > request_count
                     and companion.state.active is not None, "pending inference reached backend")
            key(0x0d)
            window._submit()
            server.release.set()
            wait_for(app, lambda: companion.state.draft == "", "cleared draft after Enter")
            QTest.qWait(200)
            check("late_result_after_enter_hidden", not companion.popup.isVisible() and not companion.can_accept_tab())

            window.title.setText(TITLE_B)
            companion.note_navigation()
            type_draft("Compare the alternatives")
            wait_for(app, lambda: companion.context_verified
                     and companion.tailer.path.name == "rollout-beta.jsonl", "task switch to beta")
            wait_for(app, lambda: companion.can_accept_tab(), "beta suggestion")
            check("task_switch_no_alpha_context", all("alpha" not in m["text"]
                  for m in server.requests[-1]["background"]))

            write_fixture(home, "collision", TITLE_B)
            companion.note_navigation()
            type_draft("Compare again")
            wait_for(app, lambda: companion.context_resolution_state == "unresolved", "ambiguous title rejected")
            wait_for(app, lambda: companion.can_accept_tab(), "ambiguous task draft-only continuation")
            check("ambiguous_title_no_old_history", server.requests[-1]["background"] == [])

            window.title.setText(TITLE_A)
            companion.note_navigation()
            type_draft("Resume the first task")
            wait_for(app, lambda: companion.context_verified
                     and companion.tailer.path.name == "rollout-alpha.jsonl", "recovery by typing")
            wait_for(app, lambda: companion.can_accept_tab(), "recovered suggestion")
            check("recovery_without_editor_click", True)

            # An obsolete socket waits for headers; the next request must reach
            # the backend and complete before the first server handler is released.
            held = server.release
            held.clear()
            request_count = len(server.requests)
            type_draft("An obsolete slow draft")
            wait_for(app, lambda: len(server.requests) > request_count, "held HTTP request")
            server.release = threading.Event()
            server.release.set()
            started = time.monotonic()
            type_draft("The replacement draft")
            wait_for(app, lambda: companion.can_accept_tab(), "replacement bypasses cancelled HTTP", timeout=2)
            check("cancelled_http_does_not_block_new_draft", time.monotonic() - started < 2 and not held.is_set())
            held.set()

            # Missing history always falls back to the current draft.
            window.title.setText("A brand new unsaved task")
            companion.note_navigation()
            type_draft("Please help me with")
            wait_for(app, lambda: companion.can_accept_tab(), "new task without history")
            check("draft_only_has_no_old_history", server.requests[-1]["background"] == [])
            check("popup_has_only_suggestion_and_tab", [label.text() for label in companion.popup.findChildren(QLabel)]
                  == [companion.state.suggestion, "Tab"])

            type_draft(DETAIL_DRAFT)
            wait_for(app, lambda: companion.can_accept_tab(), "adaptive detailed suggestion")
            detailed = companion.state.suggestion
            check("single_request_for_details", sum(r["draft"] == DETAIL_DRAFT for r in server.requests) == 1)
            check("detailed_popup_not_clipped", companion.popup.label.text() == detailed and 80 <= len(detailed) <= 160)
            check("detailed_tab_consumed", key(0x09) == 1)
            wait_for(app, lambda: window.editor.text() == DETAIL_DRAFT + detailed, "detailed native paste")
            check("only_suggestion_inserted", window.editor.text() == DETAIL_DRAFT + detailed)
            QTest.qWait(300)

            window.showMaximized()
            window.editor.setFocus()
            QTest.qWait(100)
            type_draft("The maximized fallback draft")
            # Copying from a host in this same Qt thread cannot exercise a
            # cross-process Ctrl+C. Substitute only that read; placement is real.
            with patch.object(wi, "copy_draft_fallback", lambda _: window.editor.text()):
                companion.fallback_draft()
            companion.invalidate()
            wait_for(app, lambda: companion.can_accept_tab(), "maximized fallback suggestion")
            QTest.qWait(150)
            check("maximized_fallback_popup_visible", companion.popup.isVisible())
            check("maximized_fallback_native_visible", bool(wi.user32.IsWindowVisible(int(companion.popup.winId()))))
            check("maximized_fallback_popup_on_screen", any(
                screen.availableGeometry().contains(companion.popup.frameGeometry()) for screen in app.screens()))

            # Reproduce a short popup becoming multiline before its entrance
            # ends. Read native physical window bounds against the real UIA
            # editor, including the frames after the obsolete animation ends.
            # This direct popup probe bypasses suggestion state. Invalidate UIA
            # callbacks so a late fallback read cannot hide its synthetic text.
            companion.monitor.set_active(False)
            companion.invalidate()
            companion.text_armed = False
            editor_bounds = wi.read_draft()[1]
            popup = companion.popup
            samples = []
            report["popup_geometry"] = {"editor": editor_bounds, "samples": samples}
            for initial, suggest in (("Loading model...", False), ("A short suggestion.", True)):
                popup.hide()
                popup.show_text(initial, editor_bounds, suggest=suggest)
                popup._appear.setCurrentTime(35)
                popup.show_text("First line to inspect.\nSecond line to adjust.\nThird line to verify.",
                                editor_bounds, suggest=True)
                for _ in range(15):
                    rect = wi.wintypes.RECT()
                    if not wi.user32.GetWindowRect(ctypes.c_void_p(int(popup.winId())), ctypes.byref(rect)):
                        raise AssertionError("Could not read native popup rectangle")
                    left, top, right, bottom = editor_bounds
                    if not (rect.bottom <= top or rect.top >= bottom or rect.right <= left or rect.left >= right):
                        raise AssertionError("Multiline popup overlapped the editor")
                    samples.append([rect.left, rect.top, rect.right, rect.bottom])
                    QTest.qWait(10)
                check("multiline_popup_stays_above_native_editor", popup.isVisible()
                      and rect.bottom <= editor_bounds[1])
            report["popup_geometry"] = {"editor": editor_bounds, "samples": samples}
            companion.config.save = lambda: None
            companion.toggle()
            wait_for(app, lambda: not companion.popup.isVisible(), "pause clears suggestion")
            check("pause_blocks_tab", not companion.can_accept_tab())
            check("pause_keeps_model_loaded", not any(p.get("keep_alive") == 0 for p in server.payloads))
            next(a for a in companion.menu.actions() if a.text() == tr("release_model")).trigger()
            wait_for(app, lambda: companion.releasing_backend is None, "manual release from tray")
            check("tray_release_succeeded", companion.model_released and not companion.backend_error)
            count = len(server.payloads)
            companion.toggle()
            QTest.qWait(300)
            check("release_stays_unloaded_until_edit", len(server.payloads) == count and not companion.ready)
            type_draft("A new draft after manual release")
            wait_for(app, lambda: companion.can_accept_tab(), "reload after manual release")
            check("manual_release_recovers_completion", companion.ready and not companion.model_released)
            check("requests_keep_model_resident", all(p["keep_alive"] == -1 for p in server.payloads if p.get("messages")))
            report["status"] = "PASS"
    except Exception as exc:
        report["errors"].append(f"{type(exc).__name__}: {exc}")
        window.grab().save(str(output / "failure-fixture.png"))
        if companion is not None:
            report["last_state"] = {
                "draft_len": len(companion.state.draft), "context_verified": companion.context_verified,
                "context_state": companion.context_resolution_state, "draft_dirty": companion.draft_dirty,
                "popup_visible": companion.popup.isVisible(), "model_ready": companion.ready,
            }
    finally:
        server.release.set()
        if companion is not None:
            companion.timer.stop()
            companion.hotkey_timer.stop()
            companion.monitor.stop()
            companion.resolver.stop()
            companion.session_poller.stop()
            companion.inference.stop()
            companion.close_tab_hook()
            companion.close_mouse_hook()
            companion.ime_guard.close()
            companion.popup.close()
            companion.tray.hide()
            companion.shutdown_backend()
        window.close()
        server.shutdown()
        server.server_close()
        shutdown_logging()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        (output / "receipt.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=True), flush=True)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
