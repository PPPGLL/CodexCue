"""Manual Windows paste test in a temporary external input field."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from codex_companion import windows_input


def main() -> int:
    if sys.platform != "win32":
        return 0
    app = QApplication([])
    previous = windows_input.user32.GetForegroundWindow()
    with tempfile.TemporaryDirectory() as directory:
        ready = Path(directory) / "ready.txt"
        result = Path(directory) / "result.json"
        child = subprocess.Popen([sys.executable, str(Path(__file__).with_name("input_target.py")),
                                  str(ready), str(result)])
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not ready.exists():
                time.sleep(.05)
            if not ready.exists():
                raise RuntimeError("Test field did not start; sent no input")
            hwnd = int(ready.read_text())
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                windows_input.user32.SetForegroundWindow(hwnd)
                if windows_input.user32.GetForegroundWindow() == hwnd:
                    break
                time.sleep(.1)
            else:
                raise RuntimeError("Test field did not receive focus; sent no input")
            # Exercise the same HWND fast path used by the Tab handler.
            previous_text = app.clipboard().text()
            started = time.monotonic()
            sent = windows_input.insert_text("中文😀", app.clipboard(), expected_hwnd=hwnd)
            returned_at = time.monotonic()
            loop = QEventLoop()
            QTimer.singleShot(350, loop.quit)
            loop.exec()  # Let the clipboard restoration timer run.
            child.wait(timeout=5)
            output = json.loads(result.read_text(encoding="utf-8"))
            received = output["text"]
            latency = (output["first_text_at"] - started) * 1000 if output["first_text_at"] else None
            restored = app.clipboard().text() == previous_text
            print(f"sent={sent} received={received.encode('unicode_escape')!r} "
                  f"latency_ms={latency:.1f}" if latency is not None else
                  f"sent={sent} received={received.encode('unicode_escape')!r} latency_ms=none",
                  f"call_ms={(returned_at - started) * 1000:.1f} clipboard_restored={restored}")
            return 0 if sent and received == "中文😀" and restored else 1
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=3)
            if previous:
                windows_input.user32.SetForegroundWindow(previous)


if __name__ == "__main__":
    raise SystemExit(main())
