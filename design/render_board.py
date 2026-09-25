"""Render the interactive HTML comparison to a portable PNG contact sheet."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parent


def main() -> None:
    app = QApplication([])
    view = QWebEngineView()
    view.resize(1500, 1180)

    def loaded(ok: bool) -> None:
        if not ok:
            raise RuntimeError("Could not load design comparison")

        def capture() -> None:
            output = ROOT / "style-options.png"
            if not view.grab().save(str(output), "PNG"):
                raise RuntimeError(f"Could not save {output}")
            print(output)
            app.quit()

        QTimer.singleShot(600, capture)

    view.loadFinished.connect(loaded)
    view.load(QUrl.fromLocalFile(str(ROOT / "style-options.html")))
    view.show()
    app.exec()


if __name__ == "__main__":
    main()
