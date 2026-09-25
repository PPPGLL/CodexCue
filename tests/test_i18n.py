from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QMenu, QPushButton

from codex_companion import i18n
from codex_companion.app import Companion, SettingsDialog
from codex_companion.config import AppConfig


def test_system_display_language_selects_ui_catalog(monkeypatch):
    for languages, expected in [(["zh-Hans-CN"], "zh"),
                                (["zh-TW"], "zh"),
                                (["en-US", "zh-CN"], "en"),
                                (["ja-JP", "zh-CN"], "en")]:
        monkeypatch.setattr(i18n, "system_ui_languages", lambda: languages)
        assert i18n.ui_language() == expected
    assert i18n._STRINGS["en"].keys() == i18n._STRINGS["zh"].keys()


def test_english_settings_and_tray_menu_follow_display_language(monkeypatch):
    monkeypatch.setattr(i18n, "system_ui_languages", lambda: ["en-US"])
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(AppConfig())
    try:
        assert dialog.windowTitle() == "CodexCue · Settings"
        assert dialog.backend.itemText(0) == "Local Ollama"
        assert dialog.cloud_key.placeholderText() == "Leave blank to keep the saved key"
        assert dialog.model_note.text() == "Default: balances speed and quality."
        buttons = {button.text() for button in dialog.findChildren(QPushButton)}
        assert {"Save", "Cancel", "Download model"} <= buttons
        dialog.download_process = SimpleNamespace(
            readAllStandardOutput=lambda: b"pulling manifest 42%",
            readAllStandardError=lambda: b"",
        )
        dialog._download_model = "example:latest"
        dialog._download_tail = ""
        dialog._read_download_progress()
        assert dialog.status.text() == "Downloading example:latest… 42%"
    finally:
        dialog.close()

    class Tray:
        tooltip = ""

        def setToolTip(self, value):
            self.tooltip = value

    tray = Tray()
    companion = SimpleNamespace(
        app=app, config=AppConfig(), backend_error="", ready=True,
        context_verified=False, context_resolution_state="waiting",
        active_context_label="", tailer=None, context_ready=False,
        context_messages=[], _tray_status="ready", _tray_icons={},
        tray=tray, menu=QMenu(), toggle=lambda: None,
        open_log_folder=lambda: None, open_settings_from_shortcut=lambda: None,
    )
    companion.completion_context = lambda: Companion.completion_context(companion)
    Companion.refresh_menu(companion)
    labels = [action.text() for action in companion.menu.actions()]
    assert "Enabled" in labels
    assert "Using this draft only" in labels
    assert {"Open diagnostic log folder", "Settings", "Quit"} <= set(labels)
    assert tray.tooltip == "CodexCue · Using this draft only"
