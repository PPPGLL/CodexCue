from types import SimpleNamespace
from uuid import uuid4

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QSystemTrayIcon

from codex_companion.app import (Companion, _acquire_single_instance,
                                 _handle_shortcut_requests)


def test_left_click_opens_settings_but_context_click_does_not(monkeypatch):
    opened = []
    companion = SimpleNamespace(open_settings=lambda: opened.append(True), _settings_open=False)
    monkeypatch.setattr(QTimer, "singleShot", lambda _delay, callback: callback())

    Companion.on_tray_activated(companion, QSystemTrayIcon.Context)
    assert opened == []
    Companion.on_tray_activated(companion, QSystemTrayIcon.Trigger)
    assert opened == [True]
    companion._settings_open = True
    Companion.on_tray_activated(companion, QSystemTrayIcon.Trigger)
    Companion.on_tray_activated(companion, QSystemTrayIcon.DoubleClick)
    assert opened == [True]


def test_settings_is_nonmodal_and_does_not_represent_while_open(monkeypatch):
    presented = []
    configured = []
    companion = SimpleNamespace(config=object(), _settings_open=False,
                                _present_settings=presented.append,
                                configure_backend=lambda: configured.append(True))
    companion._on_settings_finished = lambda result: Companion._on_settings_finished(companion, result)
    created = []

    class Signal:
        def connect(self, callback):
            self.callback = callback

        def emit(self, result):
            self.callback(result)

    class Dialog:
        def __init__(self, _config):
            self.finished = Signal()
            self.deleted = False
            created.append(self)

        def deleteLater(self):
            self.deleted = True

    monkeypatch.setattr("codex_companion.app.SettingsDialog", Dialog)
    Companion.open_settings(companion)
    Companion.open_settings(companion)
    assert len(created) == 1
    assert presented == created
    assert companion._settings_open is True
    assert configured == []

    created[0].finished.emit(QDialog.Accepted)
    assert companion._settings_open is False
    assert created[0].deleted is True
    assert configured == [True]


def test_desktop_shortcut_notifies_the_running_instance():
    name = f"Local\\CodexCue.Test.{uuid4()}"
    first = _acquire_single_instance(name)
    assert first is not None
    opened = []
    try:
        assert _acquire_single_instance(name, notify_existing=False) is None
        companion = SimpleNamespace(open_settings_from_shortcut=lambda: opened.append(True))
        _handle_shortcut_requests(first[0], first[2], companion)
        assert opened == []
        assert _acquire_single_instance(name) is None
        _handle_shortcut_requests(first[0], first[2], companion)
        _handle_shortcut_requests(first[0], first[2], companion)
        assert opened == [True]
    finally:
        first[0].CloseHandle(first[1])
        first[0].CloseHandle(first[2])


def test_shortcut_brings_back_existing_settings(monkeypatch):
    calls = []
    dialog = SimpleNamespace(showNormal=lambda: calls.append("show"),
                             raise_=lambda: calls.append("raise"),
                             activateWindow=lambda: calls.append("activate"),
                             winId=lambda: 123)
    companion = SimpleNamespace(_settings_dialog=dialog,
                                open_settings=lambda: calls.append("new"))
    monkeypatch.setattr("codex_companion.app.windows_input.user32",
                        SimpleNamespace(SetForegroundWindow=lambda _hwnd: calls.append("focus")))
    Companion.open_settings_from_shortcut(companion)
    assert calls == ["show", "raise", "activate", "focus"]
