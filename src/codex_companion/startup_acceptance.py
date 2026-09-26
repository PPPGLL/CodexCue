"""Exercise the normal tray lifecycle, native visibility and duplicate launches.

No real conversation, model, clipboard, or user settings are read or changed.
The caller deliberately supplies SW_HIDE to reproduce agent/script launches.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from uuid import uuid4

from PySide6.QtCore import QTimer

from .app import run_application
from .config import AppConfig
from .diagnostics import setup_logging
from .startup import command, hidden_startup
from . import windows_input as wi


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--notify")
    parser.add_argument("--initial-settings", action="store_true")
    args = parser.parse_args(argv)
    if args.notify:
        # Exactly the normal second-launch path, including the named mutex/event.
        return run_application(instance_name=args.notify)
    if args.output is None:
        parser.error("--output is required")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    setup_logging(output / "events.log")
    name = f"Local\\CodexCue.StartupTest.{uuid4()}"
    report = {"status": "FAIL", "pid": os.getpid(), "packaged": bool(getattr(sys, "frozen", False)),
              "executable_sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
              "scope": {"normal_tray_lifecycle": True, "native_visibility": True,
                        "cross_process_settings_notification": True, "real_codex_host": False},
              "initial_settings": args.initial_settings, "checks": [], "errors": []}
    pending = []
    deadline = time.monotonic() + 20

    def ready(companion):
        def check(label, value):
            if not value:
                raise AssertionError(label)
            report["checks"].append(label)

        def visible(window):
            return bool(window and window.isVisible()
                        and wi.user32.IsWindowVisible(int(window.winId())))

        def send_notification():
            child = subprocess.Popen(command(["--startup-test", "--notify", name]),
                                     creationflags=subprocess.CREATE_NO_WINDOW)
            pending.append(child)

        def steps():
            check("startup_flag_normalized", not hidden_startup())
            check("normal_timer_running", companion.timer.isActive())
            check("shortcut_poll_running", companion.app._shortcut_timer.isActive())
            if args.initial_settings:
                yield lambda: visible(getattr(companion, "_settings_dialog", None))
                check("initial_settings_native_visible", visible(companion._settings_dialog))
                companion._settings_dialog.reject()
            else:
                check("background_does_not_open_settings", getattr(companion, "_settings_dialog", None) is None)
            send_notification()
            yield lambda: visible(getattr(companion, "_settings_dialog", None))
            dialog = companion._settings_dialog
            check("second_launch_opens_native_settings", visible(dialog))
            dialog.hide()
            check("settings_hidden_before_reactivation", not visible(dialog))
            send_notification()
            yield lambda: visible(dialog)
            check("second_launch_restores_same_dialog", companion._settings_dialog is dialog and visible(dialog))
            # A Qt dropdown is another native top-level window affected by SW_HIDE.
            from PySide6.QtWidgets import QApplication, QComboBox
            combo = dialog.findChild(QComboBox)
            combo.showPopup()
            yield lambda: visible(combo.view().window())
            check("settings_dropdown_native_visible", visible(combo.view().window()))
            combo.hidePopup()
            original_model = combo.currentText()
            for cycle in range(8):
                combo.showPopup()
                check(f"dropdown_reopen_{cycle}_immediately_visible", visible(combo.view().window()))
                check(f"dropdown_reopen_{cycle}_no_animation_snapshot", not any(
                    w.metaObject().className() == "QRollEffect" and w.isVisible()
                    for w in QApplication.topLevelWidgets()))
                yield lambda: True
                combo.hidePopup()
                yield lambda: True
                check(f"dropdown_reopen_{cycle}_stays_closed", not visible(combo.view().window()))
            check("dropdown_reopen_keeps_selected_model", combo.currentText() == original_model)
            dialog.reject()
            yield lambda: getattr(companion, "_settings_dialog", None) is None
            send_notification()
            yield lambda: visible(getattr(companion, "_settings_dialog", None))
            check("settings_reopens_after_close", visible(companion._settings_dialog))
            area = companion.app.primaryScreen().availableGeometry()
            bounds = (area.left() + 40, area.top() + 250, area.left() + 450, area.top() + 300)
            foreground = wi.user32.GetForegroundWindow()
            companion.popup.show_text(" synthetic continuation", bounds, suggest=True)
            yield lambda: visible(companion.popup)
            check("suggestion_native_visible", visible(companion.popup))
            check("suggestion_does_not_take_focus", wi.user32.GetForegroundWindow() == foreground)
            companion.popup.hide()
            companion.popup.show_text(" second continuation", bounds, suggest=True)
            yield lambda: visible(companion.popup)
            check("suggestion_reopens_native_visible", visible(companion.popup))
            companion.popup.hide()
            yield lambda: all(child.poll() is not None for child in pending)
            check("duplicate_launches_exit_successfully", all(child.returncode == 0 for child in pending))
            check("main_loop_remains_responsive", time.monotonic() - companion._previous_tick < .5)
            report["status"] = "PASS"

        iterator = steps()
        condition = None

        def tick():
            nonlocal condition
            try:
                if time.monotonic() > deadline:
                    raise TimeoutError("startup lifecycle did not complete within 20 seconds")
                if condition is not None and not condition():
                    return
                condition = next(iterator)
            except StopIteration:
                finish(0)
            except Exception as exc:
                report["errors"].append(f"{type(exc).__name__}: {exc}")
                finish(1)

        def finish(code):
            timer.stop()
            for child in pending:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
            (output / "receipt.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            companion.app.exit(code)

        timer = QTimer(companion)
        timer.timeout.connect(tick)
        timer.start(50)
        companion._startup_acceptance_timer = timer

    from .acceptance import ModelServer
    server = ModelServer()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        config = AppConfig(enabled=False, ollama_url=f"http://127.0.0.1:{server.server_port}",
                           ollama_model="qa-fixture:1")
        return run_application(background=not args.initial_settings, config=config,
                               instance_name=name, on_ready=ready)
    finally:
        server.shutdown()
        server.server_close()
