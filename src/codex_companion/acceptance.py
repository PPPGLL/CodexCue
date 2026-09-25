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
from . import windows_input as wi

TITLE_A = "补全功能的识别验证"  # Nine-character title, no visible message body.
TITLE_B = "另一个测试任务"
SUFFIX = " the current settings."
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
        if payload.get("stream"):
            self.server.requests.append(json.loads(payload["messages"][-1]["content"]))
            self.server.release.wait(5)
        data = json.loads(payload["messages"][-1]["content"]) if payload.get("messages") else {"anchor": ""}
        suffix = "。" + "".join(DETAIL_ITEMS) if data.get("draft") == DETAIL_DRAFT else SUFFIX
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
        window.editor.setText(text)
        companion._hook_key_activity()
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
                report["model_results"] = []
                drafts = ["先不要修改文件，请先", "Please inspect the configu", "这个函数的返回值应该",
                          "帮我比较这两个实现的", "Before running the tests, please", "这个问题解决了吗？",
                          "我觉得", "你好"]
                detailed_drafts = {"这个表格的展示方式不太好", "导出的时候总是不知道有没有成功，改一下",
                                   "先不要动代码，帮我看看这个登录流程有什么问题"}
                drafts.extend(detailed_drafts)
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
                    if draft.endswith("？"):
                        check("complete_question_has_no_continuation", not suffix and not companion.popup.isVisible())
                    else:
                        check("live_model_suffix_visible", bool(suffix) and companion.can_accept_tab())
                        check("live_model_keeps_user_voice", not any(text in suffix for text in
                              ("有什么我可以帮", "我可以帮你", "好的，我会", "我来帮你")))
                        if draft in detailed_drafts:
                            check("rough_request_expanded", 60 <= len(suffix) <= 180)
                            check("details_finish_a_sentence", suffix.endswith(("。", ".", "？", "?", "！", "!")))
                            check("details_do_not_invent_numbers", not any(c.isdigit() for c in suffix))
                        if draft.startswith("先不要动代码"):
                            check("analysis_only_stays_analysis", "修改后" not in suffix and "调整后" not in suffix)
                        key(0x09)
                        wait_for(app, lambda: window.editor.text() == draft + suffix, "live suffix native paste")
                        check("live_model_paste_does_not_send", window.submissions == 0)
                        QTest.qWait(300)  # Let the production clipboard restoration finish.
                # Reuse the actual packaged backend with adversarial synthetic
                # history, so semantic regression is checked beyond text length.
                from .quality_checks import check_context_grounding
                quality_results = []
                quality_errors = []
                quality_done = threading.Event()
                def check_quality():
                    try:
                        quality_results.extend(check_context_grounding(companion.backend))
                    except Exception as exc:
                        quality_errors.append(type(exc).__name__)
                    finally:
                        quality_done.set()
                threading.Thread(target=check_quality, daemon=True).start()
                wait_for(app, quality_done.is_set, "quoted-rule model regressions", timeout=40)
                report["context_grounding_results"] = quality_results
                check("quoted_rules_do_not_become_suggestions", not quality_errors and len(quality_results) == 13
                      and all(row["status"] == "PASS" for row in quality_results))

                # Verify the actual server unloads this model after idle and on
                # explicit release; no model is installed or downloaded here.
                companion.backend.keep_alive_seconds = 1
                warmed = threading.Event()
                def warm_short():
                    companion.backend.warm()
                    warmed.set()
                threading.Thread(target=warm_short, daemon=True).start()
                wait_for(app, warmed.is_set, "short model residency warm", timeout=30)
                def resident():
                    response = companion.backend.client.get(companion.backend.base_url + "/api/ps", timeout=2)
                    response.raise_for_status()
                    return any(m.get("name") == args.live_model for m in response.json().get("models", []))
                check("live_model_resident_before_idle", resident())
                deadline = time.monotonic() + 6
                while resident() and time.monotonic() < deadline:
                    QTest.qWait(150)
                check("live_model_idle_release", not resident())
                warmed.clear()
                companion.backend.keep_alive_seconds = 60
                threading.Thread(target=warm_short, daemon=True).start()
                wait_for(app, warmed.is_set, "model reloaded", timeout=30)
                release = companion.backend.release_async()
                wait_for(app, lambda: not release.is_alive(), "explicit model release")
                check("live_model_explicit_release", not resident())
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
            companion.config.save = lambda: None
            companion.toggle()
            wait_for(app, lambda: not companion.popup.isVisible(), "pause clears suggestion")
            check("pause_blocks_tab", not companion.can_accept_tab())
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
            if companion.backend:
                companion.backend.close()
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
