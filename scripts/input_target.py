"""Temporary GUI target used only by check_input.py."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLineEdit


def main() -> None:
    ready, result = map(Path, sys.argv[1:3])
    app = QApplication([])
    editor = QLineEdit()
    editor.setWindowTitle("Codex Companion input smoke test")
    editor.resize(380, 60)
    editor.show()
    editor.raise_()
    editor.activateWindow()
    editor.setFocus()
    first_text_at = [None]
    def changed(value: str) -> None:
        if value and first_text_at[0] is None:
            first_text_at[0] = time.monotonic()
        if value == "中文😀":
            QTimer.singleShot(450, app.quit)
    editor.textChanged.connect(changed)
    ready.write_text(str(int(editor.winId())), encoding="utf-8")
    QTimer.singleShot(2500, app.quit)
    app.exec()
    result.write_text(json.dumps({"text": editor.text(), "first_text_at": first_text_at[0]},
                                 ensure_ascii=True), encoding="utf-8")


if __name__ == "__main__":
    main()
