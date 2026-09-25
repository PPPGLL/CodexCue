from __future__ import annotations

import ctypes
import sys
import os
import hashlib
import re
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

from PySide6.QtCore import (QEasingCurve, QObject, QParallelAnimationGroup, QPoint,
                            QPropertyAnimation, QRect, Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFormLayout,
                               QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
                               QLabel, QLineEdit, QListView, QMenu, QMessageBox, QPushButton, QSpinBox,
                               QSystemTrayIcon, QVBoxLayout, QWidget)

from .branding import app_icon, render_mark, tray_icon
from .config import (AppConfig, KEYRING_SERVICE, OLLAMA_MODEL_CHOICES,
                     default_sessions_root,
                     existing_config_path, get_cloud_key)
from .diagnostics import log_event, log_path, setup_logging
from .i18n import tr
from .model import OllamaBackend, SuggestionRequest, make_backend
from .sessions import SessionIndex, SessionTailer, match_visible_session
from .state import EditorSnapshot, RequestToken, SuggestionState
from . import windows_input


class Bridge(QObject):
    observed = Signal(int, object, object, float)
    context_changed = Signal(object, int, object)
    finished = Signal(object, object)
    failed = Signal(object, str)
    warmed = Signal(object, bool, str)
    session_resolved = Signal(int, int, object)
    key_activity = Signal()
    mouse_activity = Signal(object, object)
    navigation = Signal()
    accept_requested = Signal()


class DraftMonitor:
    """Read UI Automation on a COM thread so a slow read cannot stall typing."""

    def __init__(self, bridge: Bridge, last_typing_at=lambda: 0.0,
                 last_activity_kind=lambda: "keyboard") -> None:
        self.bridge = bridge
        self.last_typing_at = last_typing_at
        self.last_activity_kind = last_activity_kind
        self.generation = 0
        self.active = False
        self.stopping = threading.Event()
        self.changed = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def set_active(self, active: bool) -> None:
        self.generation += 1
        self.active = active
        self.changed.set()

    def wake(self) -> None:
        self.changed.set()

    def stop(self) -> None:
        self.stopping.set()
        self.changed.set()

    def run(self) -> None:
        import ctypes

        # comtypes (used by uiautomation) initializes STA by default.
        initialized = ctypes.windll.ole32.CoInitializeEx(None, 2) in (0, 1)
        previous: object = object()
        previous_generation = -1
        previous_typing_at = -1.0
        settling_for = None
        baseline = None
        settle_retries = 0
        try:
            while not self.stopping.is_set():
                # Consume the wake before reading. Activity that arrives during
                # UIA stays set for the next iteration.
                self.changed.clear()
                generation = self.generation
                if self.active:
                    typing_at = self.last_typing_at()
                    quiet = 0.15 if self.last_activity_kind() == "mouse" else 0.3
                    quiet_remaining = quiet - (time.monotonic() - typing_at)
                    if quiet_remaining > 0:
                        self.changed.wait(quiet_remaining)
                        self.changed.clear()
                        continue
                    hwnd_before = windows_input.user32.GetForegroundWindow()
                    if settling_for != (generation, typing_at):
                        settling_for = (generation, typing_at)
                        baseline = previous
                        settle_retries = 0
                    read_started = time.monotonic()
                    read = windows_input.read_draft()
                    log_event("draft_read", latency_ms=round((time.monotonic() - read_started) * 1000),
                              shown=read is not None)
                    latest_typing_at = self.last_typing_at()
                    if (latest_typing_at != typing_at
                            or time.monotonic() - latest_typing_at < quiet):
                        continue  # A key arrived during the UIA call; discard that snapshot.
                    hwnd_after = windows_input.user32.GetForegroundWindow()
                    if hwnd_before != hwnd_after:
                        self.changed.set()
                        continue  # The draft and window must come from the same focus snapshot.
                    hwnd = hwnd_after if read else 0
                    observation = (read, hwnd)
                    if (observation != previous or generation != previous_generation
                            or typing_at != previous_typing_at):
                        self.bridge.observed.emit(generation, read, hwnd, typing_at)
                        previous = observation
                        previous_generation = generation
                        previous_typing_at = typing_at
                    # Electron can report the pre-key value once after a quiet
                    # period. Retry a known editor briefly, then return to idle.
                    # Without this, an unchanged snapshot waits for another key
                    # forever, even after the UIA value finally commits.
                    if (typing_at > 0 and settle_retries < 2
                            and isinstance(baseline, tuple) and baseline[0] is not None
                            and baseline[1] == hwnd_before
                            and (read is None or read[0] == baseline[0][0])):
                        settle_retries += 1
                        self.changed.wait(.08)
                        continue
                # Hooks wake the monitor for keyboard, mouse, and focus input.
                # Repeated UIA reads while idle can stall the Codex renderer.
                if self.stopping.is_set():
                    break
                self.changed.wait()
        finally:
            if initialized:
                ctypes.windll.ole32.CoUninitialize()


class SessionPoller:
    """Parse growing Codex session files away from the Qt input thread."""

    def __init__(self, bridge: Bridge) -> None:
        self.bridge = bridge
        self.condition = threading.Condition()
        self.tailer: SessionTailer | None = None
        self.stopping = False
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def set_tailer(self, tailer: SessionTailer) -> None:
        with self.condition:
            self.tailer = tailer
            self.condition.notify()

    def stop(self) -> None:
        with self.condition:
            self.stopping = True
            self.condition.notify()

    def run(self) -> None:
        previous: SessionTailer | None = None
        while True:
            with self.condition:
                while self.tailer is None and not self.stopping:
                    self.condition.wait()
                if self.stopping:
                    return
                tailer = self.tailer
            changed = tailer.poll()
            if changed or tailer is not previous:
                self.bridge.context_changed.emit(
                    tailer, tailer.revision, tailer.context(max_messages=6, max_chars=1200))
                previous = tailer
            with self.condition:
                if self.tailer is tailer and not self.stopping:
                    self.condition.wait(0.4)


class TaskResolver:
    """Match visible Codex conversation text to one local session off the UI thread."""

    def __init__(self, bridge: Bridge) -> None:
        self.bridge = bridge
        self.index = SessionIndex(default_sessions_root())
        self.generation = 0
        self.changed = threading.Event()
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def wake(self) -> None:
        self.generation += 1
        self.changed.set()

    def invalidate(self) -> None:
        """Reject a scan already in flight without starting another scan."""
        self.generation += 1

    def stop(self) -> None:
        self.stopping.set()
        self.changed.set()

    def run(self) -> None:
        import ctypes

        initialized = ctypes.windll.ole32.CoInitializeEx(None, 2) in (0, 1)
        try:
            try:
                self.index.list()  # Build the local index while the model warms.
            except Exception as exc:
                log_event("context_index_failed", error_type=type(exc).__name__.lower())
            while not self.stopping.is_set():
                self.changed.wait()
                self.changed.clear()
                if self.stopping.is_set():
                    return
                generation = self.generation
                if self.stopping.wait(.08):
                    return  # Focus settles after a mouse click.
                if generation != self.generation:
                    continue
                hwnd = int(windows_input.user32.GetForegroundWindow() or 0)
                if not windows_input._supported_foreground():
                    continue
                started = time.monotonic()
                visible = []
                match = None
                infos = None
                uia_ms = 0
                index_ms = 0
                try:
                    uia_started = time.monotonic()
                    visible = windows_input.visible_conversation_texts()
                    uia_ms += round((time.monotonic() - uia_started) * 1000)
                    if generation != self.generation:
                        continue
                    if visible:
                        index_started = time.monotonic()
                        previous_scan = self.index.paths_at
                        infos = self.index.candidates(visible)
                        match = match_visible_session(visible, infos)
                        if match is None and self.index.paths_at == previous_scan:
                            infos = self.index.candidates(visible, force_refresh=True)
                            match = match_visible_session(visible, infos)
                        index_ms += round((time.monotonic() - index_started) * 1000)
                    if match is None:
                        if self.stopping.wait(.08):
                            return
                        if (generation != self.generation or hwnd != int(
                                windows_input.user32.GetForegroundWindow() or 0)
                                or not windows_input._supported_foreground()):
                            continue
                        uia_started = time.monotonic()
                        retry_visible = windows_input.visible_conversation_texts()
                        uia_ms += round((time.monotonic() - uia_started) * 1000)
                        if generation != self.generation:
                            continue
                        if retry_visible:
                            visible = retry_visible
                            index_started = time.monotonic()
                            loaded_now = infos is None
                            previous_scan = self.index.paths_at
                            if infos is None:
                                infos = self.index.candidates(visible)
                            match = match_visible_session(visible, infos)
                            if match is None and loaded_now and self.index.paths_at == previous_scan:
                                infos = self.index.candidates(visible, force_refresh=True)
                                match = match_visible_session(visible, infos)
                            index_ms += round((time.monotonic() - index_started) * 1000)
                except Exception as exc:
                    log_event("context_resolution_failed", error_type=type(exc).__name__.lower())
                    match = None
                if not visible:
                    editor_focused = windows_input.focused_codex_editor()
                    log_event("context_probe_skipped", hwnd=hwnd,
                              reason=("no_visible_text" if editor_focused
                                      else "editor_not_focused"))
                    if editor_focused and generation == self.generation:
                        self.bridge.session_resolved.emit(generation, hwnd, None)
                    continue
                log_event("context_probe", hwnd=hwnd, visible_count=len(visible),
                          visible_chars=sum(len(value) for value in visible),
                          shown=match is not None, uia_ms=uia_ms, index_ms=index_ms,
                          latency_ms=round((time.monotonic() - started) * 1000))
                if generation == self.generation:
                    self.bridge.session_resolved.emit(generation, hwnd, match)
        finally:
            if initialized:
                ctypes.windll.ole32.CoUninitialize()


class InferenceWorker:
    """One model request at a time; a newer draft replaces any queued request."""

    def __init__(self, bridge: Bridge) -> None:
        self.bridge = bridge
        self.condition = threading.Condition()
        self.pending = None
        self.cancel: threading.Event | None = None
        self.stopping = False
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def submit(self, token: RequestToken, request: SuggestionRequest, backend) -> threading.Event:
        event = threading.Event()
        with self.condition:
            if self.cancel:
                self.cancel.set()
            self.cancel = event
            self.pending = (token, request, backend, event)
            self.condition.notify()
        return event

    def stop(self) -> None:
        with self.condition:
            self.stopping = True
            if self.cancel:
                self.cancel.set()
            self.condition.notify()

    def run(self) -> None:
        while True:
            with self.condition:
                while self.pending is None and not self.stopping:
                    self.condition.wait()
                if self.stopping:
                    return
                token, request, backend, cancel = self.pending
                self.pending = None
            if cancel.is_set():
                continue
            try:
                result = backend.suggest(request, lambda _: None, cancel)
                self.bridge.finished.emit(token, result)
            except Exception as exc:
                self.bridge.failed.emit(token, str(exc))


def popup_position(bounds: tuple[int, int, int, int], width: int, height: int, screens,
                   *, fallback: bool = False) -> QPoint | None:
    """Place the popup outside the editor, accounting for physical UIA pixels."""
    if not screens or width <= 0 or height <= 0:
        return None
    cx = (bounds[0] + bounds[2]) // 2
    cy = (bounds[1] + bounds[3]) // 2
    selected = screens[0]
    for screen in screens:
        geometry = screen.geometry()
        ratio = screen.devicePixelRatio()
        physical = QRect(geometry.x(), geometry.y(),
                         round(geometry.width() * ratio), round(geometry.height() * ratio))
        if physical.contains(cx, cy):
            selected = screen
            break
    geometry = selected.geometry()
    ratio = selected.devicePixelRatio()
    left = geometry.x() + round((bounds[0] - geometry.x()) / ratio)
    right = geometry.x() + round((bounds[2] - geometry.x()) / ratio)
    top = geometry.y() + round((bounds[1] - geometry.y()) / ratio)
    bottom = geometry.y() + round((bounds[3] - geometry.y()) / ratio)
    editor = QRect(left, top, max(1, right - left), max(1, bottom - top))
    area = selected.availableGeometry().adjusted(6, 6, -6, -6)
    if area.width() < width or area.height() < height:
        return None
    if fallback:
        # The fallback bounds describe the host window, not its composer. Keep
        # the suggestion on screen near the top right, away from the composer.
        return QPoint(area.right() - width + 1, area.top())

    def clamp(value: int, low: int, high: int) -> int:
        return max(low, min(value, high))

    x = clamp(left, area.left(), area.right() - width + 1)
    y = clamp(top, area.top(), area.bottom() - height + 1)
    gap = 8
    candidates = [
        QPoint(x, bottom + gap),
        QPoint(x, top - height - gap),
        QPoint(right + gap, y),
        QPoint(left - width - gap, y),
    ]
    for point in candidates:
        popup = QRect(point.x(), point.y(), width, height)
        if area.contains(popup) and not popup.intersects(editor):
            return point
    return None


class ActivityGlyph(QWidget):
    """The loading dots animate; the actual suggestion never pulses or changes."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(20, 20)
        self._mark = render_mark(18)
        self._loading = False
        self._frame = 0
        self._timer = QTimer(self)
        self._timer.setInterval(180)
        self._timer.timeout.connect(self._advance)

    def set_loading(self, loading: bool) -> None:
        if self._loading == loading:
            return
        self._loading = loading
        if loading:
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def _advance(self) -> None:
        self._frame = (self._frame + 1) % 3
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._loading:
            painter.setPen(Qt.NoPen)
            for index, x in enumerate((5, 10, 15)):
                color = QColor("#34424A")
                color.setAlpha(230 if index == self._frame else 68)
                painter.setBrush(color)
                painter.drawEllipse(QPoint(x, 10), 2, 2)
        else:
            painter.drawPixmap(1, 1, self._mark)
        painter.end()


class SuggestionPopup(QWidget):
    accepted = Signal()

    def __init__(self) -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint |
                         Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(338)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 3, 5, 7)
        card = QFrame()
        card.setObjectName("suggestionCard")
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(10, 7, 9, 7)
        card_layout.setSpacing(8)
        self.glyph = ActivityGlyph()
        card_layout.addWidget(self.glyph)
        self.label = QLabel()
        font = QFont("Microsoft YaHei UI")
        font.setPointSizeF(9.5)
        self.label.setFont(font)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.NoTextInteraction)
        self.label.setFixedWidth(226)
        card_layout.addWidget(self.label, 1)
        self.hint = QLabel("Tab")
        self.hint.setObjectName("hint")
        self.hint.setFont(QFont("Segoe UI", 8, QFont.DemiBold))
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setFixedSize(36, 22)
        card_layout.addWidget(self.hint)
        layout.addWidget(card)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(12)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(20, 35, 45, 35))
        card.setGraphicsEffect(shadow)
        self.setStyleSheet(
            "QFrame#suggestionCard {background:rgba(252,253,253,248);"
            "border:1px solid #BBC6CB;border-radius:9px;}"
            "QLabel {color:#26323B;background:transparent;border:0;}"
            "QLabel#hint {color:#45525B;background:#F2F4F5;"
            "border:1px solid #D1D9DD;border-radius:5px;}"
        )
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._rise = QPropertyAnimation(self, b"pos", self)
        self._appear = QParallelAnimationGroup(self)
        for animation in (self._fade, self._rise):
            animation.setDuration(105)
            animation.setEasingCurve(QEasingCurve.OutCubic)
            self._appear.addAnimation(animation)

    def show_text(self, text: str, bounds: tuple[int, int, int, int] | None,
                  *, suggest: bool = False, fallback: bool = False) -> None:
        if not bounds:
            self.hide()
            return
        preview = text
        # Detailed requests need a wider card so the full insert stays readable.
        screen = QApplication.screenAt(QPoint(bounds[0], bounds[3])) or QApplication.primaryScreen()
        width = 480 if len(preview) > 80 else 338
        if screen is not None:
            width = min(width, screen.availableGeometry().width() - 16)
        self.setFixedWidth(width)
        text_width = max(1, width - 112)
        self.label.setFixedWidth(text_width)
        self.label.setTextFormat(Qt.PlainText)
        self.label.setText(preview)
        self.label.setToolTip(text if preview != text else "")
        # An explicit height keeps the card compact even when Qt's preferred
        # size assumes a wider label and would otherwise clip the text.
        wrapped = self.label.fontMetrics().boundingRect(
            QRect(0, 0, text_width, 1000), Qt.TextWordWrap, preview)
        line_height = self.label.fontMetrics().lineSpacing()
        self.label.setFixedHeight(max(line_height, wrapped.height()))
        self.hint.setVisible(suggest)
        self.adjustSize()
        point = popup_position(bounds, self.width(), self.height(), QApplication.screens(), fallback=fallback)
        if point is None:
            log_event("popup_not_placed", suggestion_len=len(text), bounds=bounds)
            self.hide()
        else:
            was_visible = self.isVisible()
            self.glyph.set_loading(not suggest)
            if was_visible:
                self.move(point)
            else:
                start = QPoint(point.x(), point.y() + 4)
                screen = QApplication.screenAt(point)
                if screen is None or not screen.availableGeometry().contains(
                        QRect(start, self.size())):
                    start = point
                self.move(start)
                self.setWindowOpacity(0.82)
            self.show()
            if not was_visible:
                self._appear.stop()
                self._fade.setStartValue(0.82)
                self._fade.setEndValue(1.0)
                self._rise.setStartValue(start)
                self._rise.setEndValue(point)
                self._appear.start()
            log_event("popup_shown", suggestion_len=len(text), bounds=bounds,
                      visible=bool(windows_input.user32.IsWindowVisible(int(self.winId()))))

    def hideEvent(self, event) -> None:
        self._appear.stop()
        self.glyph.set_loading(False)
        self.setWindowOpacity(1.0)
        super().hideEvent(event)


class InkComboBox(QComboBox):
    """A compact combo box matching the settings fields and menu palette."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("inkCombo")
        view = QListView(self)
        view.setSpacing(2)
        view.setStyleSheet(
            "QListView {background:#FFFFFF;color:#26323B;"
            "border:1px solid #C6D0D4;border-radius:7px;padding:5px;outline:0;}"
            "QListView::item {min-height:25px;padding:4px 9px;border-radius:4px;}"
            "QListView::item:hover {background:#F1F4F5;}"
            "QListView::item:selected {background:#E8EEF0;color:#26323B;}"
        )
        self.setView(view)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#627680" if self.isEnabled() else "#AEB8BD"),
                            1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        x, y = self.width() - 18, self.height() // 2
        painter.drawLine(x - 4, y - 2, x, y + 2)
        painter.drawLine(x, y + 2, x + 4, y - 2)
        painter.end()


class SettingsDialog(QDialog):
    models_checked = Signal(int, object, object)
    save_checked = Signal(int, object)

    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("settings_title"))
        self.setMinimumWidth(520)
        self.setWindowIcon(app_icon())
        self.config = config
        self.download_process = None
        self._model_check_id = 0
        self._save_check_id = 0
        self._pending_save = None
        self.models_checked.connect(self._on_models_checked)
        self.save_checked.connect(self._on_save_checked)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(23, 21, 23, 20)
        layout.setSpacing(15)
        header = QHBoxLayout()
        mark = QLabel()
        mark.setPixmap(render_mark(40))
        header.addWidget(mark)
        heading = QVBoxLayout()
        from . import __version__
        title = QLabel(f"CodexCue {__version__}")
        title.setObjectName("settingsTitle")
        subtitle = QLabel(tr("settings_subtitle"))
        subtitle.setObjectName("settingsSubtitle")
        heading.addWidget(title)
        heading.addWidget(subtitle)
        header.addLayout(heading)
        header.addStretch()
        layout.addLayout(header)
        form = QFormLayout()
        form.setSpacing(10)
        self.backend = InkComboBox()
        self.backend.addItems([tr("backend_local"), tr("backend_cloud")])
        self.backend.setCurrentIndex(0 if config.backend == "ollama" else 1)
        self.ollama_url = QLineEdit(config.ollama_url)
        self.ollama_model = InkComboBox()
        self.ollama_model.setEditable(True)
        self.ollama_model.addItems([name for name, _ in OLLAMA_MODEL_CHOICES])
        self.ollama_model.setCurrentText(config.ollama_model)
        self.ollama_model.currentTextChanged.connect(self.update_model_note)
        self.cloud_url = QLineEdit(config.cloud_base_url)
        self.cloud_model = QLineEdit(config.cloud_model)
        self.cloud_key = QLineEdit()
        self.cloud_key.setEchoMode(QLineEdit.Password)
        self.cloud_key.setPlaceholderText(tr("keep_key"))
        self.model_idle = QSpinBox()
        self.model_idle.setRange(0, 3600)
        self.model_idle.setValue(config.model_idle_seconds)
        self.model_idle.setSuffix(tr("seconds"))
        self.model_idle.setToolTip(tr("idle_help"))
        for label, widget in [(tr("field_backend"), self.backend),
                              (tr("field_ollama_url"), self.ollama_url),
                              (tr("field_local_model"), self.ollama_model),
                              ("API Base URL", self.cloud_url),
                              (tr("field_cloud_model"), self.cloud_model),
                              ("API Key", self.cloud_key),
                              (tr("field_model_idle"), self.model_idle)]:
            form.addRow(label, widget)
        layout.addLayout(form)
        self.model_note = QLabel()
        self.model_note.setObjectName("modelNote")
        self.model_note.setWordWrap(True)
        layout.addWidget(self.model_note)
        self.update_model_note()
        self.status = QLabel(tr("config_recovered" if getattr(config, "recovery_required", False)
                                else "model_install_notice"))
        self.status.setObjectName("settingsStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        for title, callback in [(tr("refresh_models"), self.check_ollama),
                                (tr("install_help"), lambda: webbrowser.open("https://ollama.com/download/windows")),
                                (tr("download_model"), self.download_model)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        buttons = QHBoxLayout()
        save = QPushButton(tr("save"))
        save.setObjectName("primaryButton")
        save.clicked.connect(self.save)
        self.save_button = save
        cancel = QPushButton(tr("cancel"))
        cancel.clicked.connect(self.reject)
        buttons.addStretch()
        buttons.addWidget(save)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        self.setStyleSheet(
            "QDialog {background:#F8F9F9;color:#26323B;}"
            "QLabel {color:#26323B;}"
            "QLabel#settingsTitle {font-size:16px;font-weight:700;}"
            "QLabel#settingsSubtitle {color:#697781;font-size:11px;}"
            "QLabel#modelNote {color:#667681;font-size:10px;}"
            "QLabel#settingsStatus {color:#495963;background:#EFF2F3;"
            "border:1px solid #D7DEE1;border-radius:7px;padding:9px;}"
            "QLineEdit {background:#FFFFFF;color:#26323B;"
            "border:1px solid #C6D0D4;border-radius:6px;padding:7px;}"
            "QLineEdit:focus {border:1px solid #667D89;}"
            "QComboBox#inkCombo {background:#FFFFFF;color:#26323B;"
            "border:1px solid #C6D0D4;border-radius:6px;"
            "padding:7px 34px 7px 10px;min-height:20px;}"
            "QComboBox#inkCombo:hover {border-color:#AABAC1;}"
            "QComboBox#inkCombo:focus {border-color:#667D89;}"
            "QComboBox#inkCombo::drop-down {width:30px;border:0;background:transparent;}"
            "QComboBox#inkCombo::down-arrow {image:none;width:0;height:0;}"
            "QComboBox#inkCombo QLineEdit {border:0;background:transparent;"
            "color:#26323B;padding:0;}"
            "QPushButton {background:#FFFFFF;color:#34434C;"
            "border:1px solid #C8D1D5;border-radius:6px;padding:7px 13px;}"
            "QPushButton:hover {background:#EFF2F3;}"
            "QPushButton#primaryButton {background:#27333C;color:#FFFFFF;"
            "border:1px solid #27333C;font-weight:700;}"
            "QPushButton#primaryButton:hover {background:#3A4A54;}"
        )

    def update_model_note(self) -> None:
        selected = self.ollama_model.currentText().strip()
        notes = dict(OLLAMA_MODEL_CHOICES)
        self.model_note.setText(tr(notes.get(selected, "model_custom")))

    def check_ollama(self) -> None:
        self._model_check_id += 1
        check_id = self._model_check_id
        url = self.ollama_url.text().strip()
        model = self.ollama_model.currentText().strip()
        self.status.setText(tr("checking_models"))

        def check() -> None:
            try:
                backend = OllamaBackend(url, model)
                try:
                    result = backend.list_models()
                finally:
                    backend.close()
                self.models_checked.emit(check_id, result, None)
            except Exception as exc:
                self.models_checked.emit(check_id, None, str(exc))

        threading.Thread(target=check, daemon=True).start()

    def _on_models_checked(self, check_id: int, models: object, error: object) -> None:
        if check_id != self._model_check_id:
            return
        if error is not None:
            self.status.setText(tr("ollama_unavailable", error=error))
            return
        selected = self.ollama_model.currentText()
        known = {name for name, _ in models}
        for name, size in models:
            if self.ollama_model.findText(name) < 0:
                self.ollama_model.addItem(name)
            index = self.ollama_model.findText(name)
            self.ollama_model.setItemData(index, tr("installed_size", size=size / 1024 ** 3), Qt.ToolTipRole)
        self.ollama_model.setCurrentText(selected)
        wanted = selected if ":" in selected else f"{selected}:latest"
        self.status.setText(tr("ollama_installed" if wanted in known else "ollama_missing",
                               count=len(models)))

    def download_model(self) -> None:
        from PySide6.QtCore import QProcess, QProcessEnvironment

        if self.download_process is not None:
            return
        model = self.ollama_model.currentText().strip()
        if not model:
            self.status.setText(tr("enter_model"))
            return
        self.download_process = QProcess(self)
        self._download_model = model
        self._download_tail = ""
        process = self.download_process
        executable = Path(self.config.ollama_executable)
        process.setProgram(str(executable) if executable.is_file() else "ollama")
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("OLLAMA_HOST", "127.0.0.1:11434")
        if self.config.ollama_models_dir:
            environment.insert("OLLAMA_MODELS", self.config.ollama_models_dir)
        process.setProcessEnvironment(environment)
        process.setArguments(["pull", model])
        process.readyReadStandardError.connect(self._read_download_progress)
        process.readyReadStandardOutput.connect(self._read_download_progress)
        process.errorOccurred.connect(lambda error: self._download_error(process, error))
        process.finished.connect(lambda code, _: self._download_done(code, process))
        process.start()
        self.status.setText(tr("downloading_model", model=model))

    def _read_download_progress(self) -> None:
        process = self.download_process
        if process is None:
            return
        output = bytes(process.readAllStandardOutput()) + bytes(process.readAllStandardError())
        self._download_tail = (self._download_tail + output.decode(errors="replace"))[-300:]
        percentages = re.findall(r"(?<!\d)(?:100|[1-9]?\d)%", self._download_tail)
        if percentages:
            self.status.setText(tr("downloading_model_progress", model=self._download_model,
                                   percent=percentages[-1][:-1]))

    def _download_error(self, process, error) -> None:
        from PySide6.QtCore import QProcess

        if process is not self.download_process:
            return
        self.status.setText(tr("ollama_run_failed"))
        if error == QProcess.FailedToStart:
            self.download_process = None
            process.deleteLater()

    def _download_done(self, code: int, process=None) -> None:
        if process is not None and process is not self.download_process:
            return
        completed = self.download_process
        self.download_process = None
        if completed is not None:
            completed.deleteLater()
        if code == 0:
            self.check_ollama()
        else:
            self.status.setText(tr("model_download_failed", code=code))

    def save(self) -> None:
        backend = "ollama" if self.backend.currentIndex() == 0 else "cloud"
        try:
            if backend == "ollama":
                url = self.ollama_url.text().strip()
                model = self.ollama_model.currentText().strip()
                if not model:
                    raise ValueError(tr("enter_model"))
                self._save_check_id += 1
                check_id = self._save_check_id
                self._pending_save = (url, model)
                self.save_button.setEnabled(False)
                self.status.setText(tr("checking_models"))

                def check() -> None:
                    try:
                        probe = OllamaBackend(url, model)
                        try:
                            _, installed = probe.available()
                        finally:
                            probe.close()
                        result = "" if installed else "model_missing"
                    except Exception as exc:
                        result = str(exc)
                    self.save_checked.emit(check_id, result)

                threading.Thread(target=check, daemon=True).start()
                return
            else:
                from urllib.parse import urlparse
                url = self.cloud_url.text().strip()
                parsed = urlparse(url)
                if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                    raise ValueError(tr("https_required"))
                if not parsed.netloc or not self.cloud_model.text().strip():
                    raise ValueError(tr("api_address_model_required"))
                import keyring
                if not self.cloud_key.text() and not get_cloud_key(url):
                    raise ValueError(tr("api_key_required"))
                if self.cloud_key.text():
                    keyring.set_password(KEYRING_SERVICE, url, self.cloud_key.text())
        except Exception as exc:
            self.status.setText(str(exc))
            return
        self._commit_config(backend)

    def _on_save_checked(self, check_id: int, error: str) -> None:
        if check_id != self._save_check_id:
            return
        self.save_button.setEnabled(True)
        pending = self._pending_save
        self._pending_save = None
        if error:
            self.status.setText(tr("selected_model_missing") if error == "model_missing" else error)
            return
        if (pending != (self.ollama_url.text().strip(), self.ollama_model.currentText().strip())
                or self.backend.currentIndex() != 0):
            return
        self._commit_config("ollama")

    def _commit_config(self, backend: str) -> None:
        from dataclasses import replace

        updates = dict(backend=backend, ollama_url=self.ollama_url.text().strip(),
                       ollama_model=self.ollama_model.currentText().strip(),
                       cloud_base_url=self.cloud_url.text().strip(),
                       cloud_model=self.cloud_model.text().strip(),
                       model_idle_seconds=self.model_idle.value())
        try:
            replace(self.config, **updates).save()
        except OSError:
            self.status.setText(tr("config_save_failed"))
            return
        for key, value in updates.items():
            setattr(self.config, key, value)
        self.config.recovery_required = False
        self.accept()


def _short_context(value: str, limit: int = 10) -> str:
    compact = " ".join(value.split())
    return compact[:limit] + ("…" if len(compact) > limit else "")


class Companion(QObject):
    def __init__(self, app: QApplication, config: AppConfig) -> None:
        super().__init__()
        self.app = app
        self.config = config
        self.bridge = Bridge()
        self.bridge.observed.connect(self.on_observed)
        self.bridge.context_changed.connect(self.on_context_changed)
        self.bridge.finished.connect(self.on_finished)
        self.bridge.failed.connect(self.on_failed)
        self.bridge.warmed.connect(self.on_warmed)
        self.bridge.session_resolved.connect(self.on_session_resolved)
        self.bridge.key_activity.connect(self.note_typing)
        self.bridge.mouse_activity.connect(self.note_mouse_activity)
        self.bridge.navigation.connect(self.note_navigation)
        self.bridge.accept_requested.connect(self.accept_suggestion)
        # The keyboard quiet period is enforced before UIA reads; do not add
        # a second debounce after the latest draft has already been captured.
        self.state = SuggestionState(debounce_seconds=0.0)
        self.popup = SuggestionPopup()
        self.bounds: tuple[int, int, int, int] | None = None
        self.last_read: tuple[str, tuple[int, int, int, int]] | None = None
        self.tailer: SessionTailer | None = None
        self.context_revision = 0
        self.context_messages = []
        self.context_ready = False
        self.context_verified = False
        self.active_context_label = ""
        self.backend = None
        self.cancel: threading.Event | None = None
        self.pending_insertion: tuple[str, str] | None = None
        self.ready = False
        self.backend_error = ""
        self.accept_hwnd = 0
        self.last_codex_hwnd = 0
        self.last_typing_at = 0.0
        self.awaiting_committed_edit = False
        self.request_started_at = 0.0
        self.draft_dirty = False
        self.input_activity_pending = False
        self.last_activity_kind = "keyboard"
        self.inserting = False
        self.fallback_down = False
        self.fallback_pending = False
        self.mouse_down = False
        self.awaiting_editor_click = False
        self.pending_click_position: tuple[int, int] | None = None
        self.context_resolution_state = "waiting"
        self.last_resolution_attempt_at = 0.0
        self.last_request_gate_reason = ""
        self.last_keyboard_log_at = 0.0
        self.fallback_mode = False
        self.text_armed = False
        self.ime_composing = False
        self.ime_guard = windows_input.ImeGuard()
        self.app.aboutToQuit.connect(self.ime_guard.close)
        self.monitor = DraftMonitor(self.bridge, lambda: self.last_typing_at,
                                    lambda: self.last_activity_kind)
        self.app.aboutToQuit.connect(self.monitor.stop)
        self.session_poller = SessionPoller(self.bridge)
        self.app.aboutToQuit.connect(self.session_poller.stop)
        self.resolver = TaskResolver(self.bridge)
        self.app.aboutToQuit.connect(self.resolver.stop)
        self.inference = InferenceWorker(self.bridge)
        self.app.aboutToQuit.connect(self.inference.stop)
        self._tray_icons = {status: tray_icon(status)
                            for status in ("ready", "loading", "paused", "error")}
        self._tray_status = "loading"
        self.tray = QSystemTrayIcon(self._tray_icons["loading"], self.app)
        self.menu = QMenu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()
        try:
            self.tab_hook = windows_input.TabHook(
                self._hook_can_accept_tab, self.bridge.accept_requested.emit,
                self._hook_key_activity, self.bridge.navigation.emit)
        except OSError as exc:
            self.tab_hook = None
            self.tray.showMessage(tr("tab_unavailable"), str(exc))
        self.app.aboutToQuit.connect(self.close_tab_hook)
        try:
            self.mouse_hook = windows_input.MouseHook(self._hook_mouse_activity)
        except OSError:
            self.mouse_hook = None  # The polling fallback below still clears suggestions.
        self.app.aboutToQuit.connect(self.close_mouse_hook)
        self.refresh_menu()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(25)
        self.hotkey_timer = QTimer(self)
        self.hotkey_timer.timeout.connect(self.check_hotkeys)
        self.hotkey_timer.start(25)
        self.configure_backend()
        self.app.aboutToQuit.connect(self.shutdown_backend)
        if config.enabled:
            self.monitor.set_active(True)

    def _hook_can_accept_tab(self) -> bool:
        hook = getattr(self, "tab_hook", None)
        return bool(hook and hook.ready and self.accept_hwnd
                    and windows_input.user32.GetForegroundWindow() == self.accept_hwnd)

    def _hook_mouse_activity(self, x: int | None, y: int | None) -> None:
        hook = getattr(self, "tab_hook", None)
        if hook:
            hook.ready = False
        self.last_typing_at = time.monotonic()
        self.last_activity_kind = "mouse"
        self.monitor.wake()
        self.bridge.mouse_activity.emit(x, y)

    def _hook_key_activity(self) -> None:
        # Publish the quiet-period timestamp before Qt handles the queued
        # signal, so a busy settings window cannot trigger a stale UIA read.
        hook = getattr(self, "tab_hook", None)
        if hook:
            hook.ready = False
        self.last_typing_at = time.monotonic()
        self.monitor.wake()
        self.bridge.key_activity.emit()

    def refresh_menu(self) -> None:
        tray_status = ("paused" if not self.config.enabled else
                       "error" if self.backend_error else
                       "ready" if self.ready else "loading")
        if tray_status != self._tray_status:
            self._tray_status = tray_status
            self.tray.setIcon(self._tray_icons[tray_status])
        self.menu.clear()
        name = self.menu.addAction("CodexCue")
        name.setEnabled(False)
        self.menu.addSeparator()
        status = self.menu.addAction(tr("enabled") if self.config.enabled else tr("paused"))
        status.triggered.connect(self.toggle)
        model_label = (tr("model_ready") if self.ready else
                       tr("model_unavailable") if self.backend_error else
                       tr("model_loading_status"))
        model_status = self.menu.addAction(model_label)
        model_status.setEnabled(False)
        if self.completion_context() == []:
            context_label = tr("draft_only_hint")
        elif not self.context_verified:
            if self.context_resolution_state == "checking":
                context_label = tr("context_checking")
            elif self.context_resolution_state == "waiting":
                context_label = tr("context_click")
            else:
                context_label = tr("context_unresolved")
        elif self.active_context_label:
            context_label = tr("context_preview", preview=_short_context(self.active_context_label))
        elif self.tailer is not None:
            context_label = tr("context_matched")
        else:
            context_label = tr("context_waiting")
        context_status = self.menu.addAction(context_label)
        context_status.setEnabled(False)
        tooltip = context_label
        if self.completion_context():
            users = sum(message.role == "user" for message in self.context_messages)
            assistants = sum(message.role == "assistant" for message in self.context_messages)
            counts = tr("message_counts", users=users, assistants=assistants)
            count_status = self.menu.addAction(counts)
            count_status.setEnabled(False)
            tooltip += f" | {counts}"
        self.tray.setToolTip(f"CodexCue · {tooltip}")
        self.menu.addSeparator()
        self.menu.addAction(tr("open_logs"), self.open_log_folder)
        self.menu.addAction(tr("settings"), self.open_settings_from_shortcut)
        self.menu.addAction(tr("quit"), self.app.quit)

    def toggle(self) -> None:
        self.config.enabled = not self.config.enabled
        self.config.save()
        self.draft_dirty = self.config.enabled
        self.text_armed = False
        self.monitor.set_active(self.config.enabled)
        self.resolver.invalidate()
        if self.config.enabled:
            self.context_verified = False
            self.context_resolution_state = "waiting"
        self.invalidate()
        if not self.config.enabled and isinstance(self.backend, OllamaBackend):
            self.backend.release_async()
        elif self.config.enabled and not self.ready:
            self.configure_backend()
        log_event("enabled_changed", enabled=self.config.enabled)
        self.refresh_menu()

    def open_log_folder(self) -> None:
        folder = log_path().parent
        if folder.is_dir():
            os.startfile(folder)
        else:
            self.tray.showMessage(tr("logs_unavailable"), tr("logs_cannot_create"))

    def _bind_resolved_session(self, path: Path) -> bool:
        if not path.is_file() or path.suffix != ".jsonl" or not path.resolve().is_relative_to(default_sessions_root().resolve()):
            return False
        was_armed = self.text_armed
        self.invalidate()
        self.last_read = None
        self.accept_hwnd = 0
        self.draft_dirty = True
        self.text_armed = was_armed
        self.tailer = SessionTailer(path)
        self.context_revision = 0
        self.context_messages = []
        self.context_ready = False
        self.session_poller.set_tailer(self.tailer)
        self.monitor.set_active(self.config.enabled)
        log_event("session_selected", session_id=hashlib.sha256(str(path).encode()).hexdigest()[:8])
        return True

    def on_session_resolved(self, generation: int, hwnd: int, info: object) -> None:
        if (not self.config.enabled
                or generation != self.resolver.generation
                or hwnd != int(windows_input.user32.GetForegroundWindow() or 0)):
            return
        previous_context = self.completion_context()
        if info is None:
            changed = self.context_verified or self.context_resolution_state != "unresolved"
            if self.context_verified:
                self.context_verified = False
                self.invalidate()
            self.context_resolution_state = "unresolved"
            log_event("context_unresolved", reason="no_unique_match")
            if changed:
                self.refresh_menu()
            return
        path = info.path
        if self.tailer is None or self.tailer.path != path:
            if not self._bind_resolved_session(path):
                self.context_verified = False
                self.context_resolution_state = "unresolved"
                self.invalidate()
                self.refresh_menu()
                return
            log_event("context_switched", session_id=hashlib.sha256(str(path).encode()).hexdigest()[:8])
        self.active_context_label = info.preview
        self.context_verified = True
        self.context_resolution_state = "matched"
        if previous_context != self.completion_context():
            self.invalidate()
        self.refresh_menu()
        self.monitor.wake()

    def on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        log_event("tray_activated", generation=int(reason.value))
        if reason == QSystemTrayIcon.Trigger and not getattr(self, "_settings_open", False):
            QTimer.singleShot(0, self.open_settings)

    @staticmethod
    def _present_settings(dialog: QDialog) -> None:
        # A tray-only app has no parent window to center the dialog. Windows can
        # otherwise reuse an off-screen placement or leave it behind the app.
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        dialog.adjustSize()
        if screen is not None:
            area = screen.availableGeometry()
            width = min(dialog.width(), area.width())
            height = min(dialog.height(), area.height())
            dialog.move(area.left() + (area.width() - width) // 2,
                        area.top() + (area.height() - height) // 2)
        dialog.showNormal()
        dialog.raise_()
        dialog.activateWindow()
        hwnd = int(dialog.winId())
        # Qt's activateWindow() can be ignored when the last active surface was
        # the notification area. The explicit click grants foreground rights.
        if sys.platform == "win32":
            windows_input.user32.SetForegroundWindow(hwnd)
        geometry = dialog.frameGeometry()
        log_event("settings_presented", hwnd=hwnd,
                  visible=bool(windows_input.user32.IsWindowVisible(hwnd)),
                  bounds=(geometry.left(), geometry.top(), geometry.right(), geometry.bottom()),
                  foreground=int(windows_input.user32.GetForegroundWindow() or 0))

    def open_settings(self) -> None:
        if getattr(self, "_settings_open", False):
            return
        self._settings_open = True
        try:
            dialog = SettingsDialog(self.config)
            self._settings_dialog = dialog
            dialog.finished.connect(self._on_settings_finished)
            self._present_settings(dialog)
            log_event("settings_opened")
        except Exception:
            self._settings_dialog = None
            self._settings_open = False
            raise

    def open_settings_from_shortcut(self) -> None:
        dialog = getattr(self, "_settings_dialog", None)
        if dialog is None:
            self.open_settings()
            return
        # An explicit shortcut/menu command may bring the existing window back.
        dialog.showNormal()
        dialog.raise_()
        dialog.activateWindow()
        if sys.platform == "win32":
            windows_input.user32.SetForegroundWindow(int(dialog.winId()))

    def _on_settings_finished(self, result: int) -> None:
        dialog = self._settings_dialog
        self._settings_dialog = None
        self._settings_open = False
        if dialog is not None:
            dialog.deleteLater()
        if result == QDialog.Accepted:
            self.configure_backend()

    def configure_backend(self) -> None:
        self.invalidate()
        self.ready = False
        self.backend_error = ""
        self.refresh_menu()
        try:
            new_backend = make_backend(self.config)
        except Exception as exc:
            self.backend_error = str(exc)
            self.refresh_menu()
            self.tray.showMessage(tr("backend_settings"), str(exc))
            return
        old = self.backend
        self.backend = new_backend
        log_event("backend_configured", backend=self.config.backend)
        if old:
            # A previous streaming request can still be unwinding here. Closing
            # its HTTP client must not hold up the settings window or input hook.
            threading.Thread(target=old.close, daemon=True).start()
        if isinstance(new_backend, OllamaBackend) and self.config.enabled:
            if (self.text_armed and not self.draft_dirty and self.state.draft.strip()
                    and not self._ime_active()):
                self.popup.show_text(tr("model_loading_popup"), self.bounds)
            def warm() -> None:
                try:
                    try:
                        _, installed = new_backend.available()
                    except Exception:
                        executable = Path(self.config.ollama_executable)
                        if not self.config.ollama_executable or not executable.is_file():
                            raise
                        env = os.environ.copy()
                        env["OLLAMA_HOST"] = "127.0.0.1:11434"
                        if self.config.ollama_models_dir:
                            env["OLLAMA_MODELS"] = self.config.ollama_models_dir
                        subprocess.Popen(
                            [str(executable), "serve"], env=env,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        for _ in range(40):
                            time.sleep(.25)
                            try:
                                _, installed = new_backend.available()
                                break
                            except Exception:
                                continue
                        else:
                            raise RuntimeError(tr("ollama_start_timeout"))
                    if not installed:
                        raise RuntimeError(tr("model_not_installed"))
                    new_backend.warm()
                    self.bridge.warmed.emit(new_backend, True, "")
                except Exception as exc:
                    self.bridge.warmed.emit(new_backend, False, str(exc))
            threading.Thread(target=warm, daemon=True).start()
        elif not isinstance(new_backend, OllamaBackend):
            self.ready = True
            self.refresh_menu()

    def shutdown_backend(self) -> None:
        self.invalidate()
        if isinstance(self.backend, OllamaBackend):
            self.backend.release_async().join(timeout=2.5)
        if self.backend:
            self.backend.close()

    def on_warmed(self, backend: object, ok: bool, message: str) -> None:
        if backend is not self.backend:
            return
        self.ready = ok
        log_event("model_warmed", shown=ok)
        self.backend_error = "" if ok else message
        self.refresh_menu()
        if not ok:
            self.popup.hide()
            self.tray.showMessage(tr("local_model_not_ready"), message)
        else:
            self.popup.hide()
            self.tick()

    def invalidate(self) -> None:
        hook = getattr(self, "tab_hook", None)
        if hook:
            hook.ready = False
        if self.cancel:
            self.cancel.set()
        self.pending_insertion = None
        self.state.observe(self.state.draft, self.state.context_revision + 1, time.monotonic())
        self.popup.hide()

    def _ime_active(self) -> bool:
        guard = getattr(self, "ime_guard", None)
        return bool(guard and guard.active_for(self.accept_hwnd))

    def on_observed(self, generation: int, read: object, hwnd: object, typing_at: float) -> None:
        if (generation != self.monitor.generation or typing_at != self.last_typing_at
                or not self.config.enabled):
            return
        if self._ime_active():
            return  # Composition text is not a committed draft.
        if read is not None and int(hwnd) != windows_input.user32.GetForegroundWindow():
            self.awaiting_editor_click = False
            log_event("observation_discarded", reason="focus_changed")
            self.invalidate()
            return
        if read is not None and int(hwnd) != self.last_codex_hwnd and self.context_verified:
            # Clear ownership before throttling the new scan. Otherwise a quick
            # window switch could temporarily reuse the previous task's history.
            self.context_verified = False
            self.context_resolution_state = "waiting"
            self.invalidate()
        clicked_editor = False
        if self.awaiting_editor_click:
            self.awaiting_editor_click = False
            bounds = read[1] if read is not None else None
            point = self.pending_click_position
            clicked_editor = (bounds is not None and point is not None
                              and bounds[0] <= point[0] < bounds[2]
                              and bounds[1] <= point[1] < bounds[3])
            self.pending_click_position = None
        if (read is not None
                and (not self.context_verified or int(hwnd) != self.last_codex_hwnd)
                and (clicked_editor or (self.last_read is None and self.last_typing_at == 0)
                     or (self.last_activity_kind == "keyboard"
                                        and self.text_armed and bool(read[0].strip())))):
            now = time.monotonic()
            retry_after = 2.0 if self.context_resolution_state == "checking" else 1.0
            if clicked_editor or now - getattr(self, "last_resolution_attempt_at", 0) >= retry_after:
                if self.context_verified:
                    self.context_verified = False
                    self.invalidate()
                self.context_resolution_state = "checking"
                self.last_resolution_attempt_at = now
                self.resolver.wake()
                self.refresh_menu()
            else:
                # Throttling must defer a retry, not silently lose the only
                # settled edit after a fast task switch. Force a fresh UIA
                # observation; never replay the cached draft as a new read.
                resolver_generation = self.resolver.generation
                def retry_if_current() -> None:
                    if (self.config.enabled and not self.context_verified
                            and self.monitor.generation == generation
                            and self.last_typing_at == typing_at
                            and self.resolver.generation == resolver_generation
                            and int(hwnd) == windows_input.user32.GetForegroundWindow()):
                        self.monitor.set_active(True)
                delay = max(1, int((retry_after - (now - self.last_resolution_attempt_at)) * 1000) + 1)
                QTimer.singleShot(delay, self, retry_if_current)
        if self.pending_insertion is not None:
            previous, _suffix = self.pending_insertion
            self.pending_insertion = None
            if read is not None and read[0] == previous:
                self.tray.showMessage(tr("insert_not_applied"), tr("confirm_codex_focus"))
        waiting_for_commit = (self.awaiting_committed_edit and read is not None
                              and read[0] == self.state.draft)
        self.draft_dirty = waiting_for_commit
        if read is None:
            if self.last_read is not None and not self.fallback_mode:
                log_event("editor_lost", reason="uia_unavailable")
                self.last_read = None
                self.accept_hwnd = 0
                self.invalidate()
            return
        previous_bounds = self.bounds
        previous_read = self.last_read
        previous_hwnd = self.accept_hwnd
        self.last_read = read
        self.fallback_mode = False
        draft, self.bounds = read
        self.accept_hwnd = int(hwnd)
        self.last_codex_hwnd = int(hwnd)
        if self.awaiting_committed_edit and not waiting_for_commit:
            self.awaiting_committed_edit = False
            self.text_armed = bool(draft.strip())
        elif waiting_for_commit:
            log_event("draft_unchanged_after_key", draft_len=len(draft), hwnd=self.accept_hwnd)
        if read != previous_read or self.accept_hwnd != previous_hwnd:
            log_event("draft_observed", draft_len=len(draft), hwnd=self.accept_hwnd,
                      bounds=self.bounds, generation=self.state.generation)
        changed = self.state.observe(draft, self.context_revision, time.monotonic())
        if changed:
            if self.cancel:
                self.cancel.set()
            self.popup.hide()
        elif (previous_bounds != self.bounds and self.state.suggestion and not self.draft_dirty
              and self.text_armed and self.context_verified and not self._ime_active()):
            self.show_suggestion(self.state.suggestion)
        if (self.context_verified and self.text_armed and not self.draft_dirty
                and self.state.draft.strip() and not self.ready
                and isinstance(self.backend, OllamaBackend) and not self._ime_active()):
            self.popup.show_text(tr("model_loading_popup"), self.bounds)

    def on_context_changed(self, tailer: SessionTailer, revision: int,
                           messages: list) -> None:
        if tailer is not self.tailer:
            return
        self.context_revision = revision
        self.context_messages = messages
        self.context_ready = True
        if self.state.observe(self.state.draft, revision, time.monotonic()):
            if self.cancel:
                self.cancel.set()
            self.popup.hide()
        self.refresh_menu()

    def check_hotkeys(self) -> None:
        if getattr(self, "mouse_hook", None) is None:
            mouse_down = any(windows_input.user32.GetAsyncKeyState(vk) & 0x8000
                             for vk in (0x01, 0x02, 0x04))
            if mouse_down and not self.mouse_down:
                point = windows_input.cursor_position()
                self.note_mouse_activity(*(point or (None, None)))
            self.mouse_down = mouse_down
        fallback = windows_input.shortcut_pressed(0x44)
        if fallback and not self.fallback_down:
            self.fallback_pending = True
        if self.fallback_pending and windows_input.modifiers_released():
            self.fallback_pending = False
            self.fallback_draft()
        self.fallback_down = fallback

    def can_accept_tab(self) -> bool:
        return (self.editor_snapshot().rejection() == "ready"
                and bool(self.state.suggestion) and self.popup.isVisible())

    def completion_context(self) -> list:
        if self.context_verified and self.context_ready and self.tailer:
            if any(message.role == "user" for message in self.context_messages):
                return self.context_messages
        return []

    def editor_snapshot(self) -> EditorSnapshot:
        return EditorSnapshot(
            enabled=self.config.enabled, inserting=self.inserting, dirty=self.draft_dirty,
            pending_activity=self.input_activity_pending, armed=self.text_armed,
            composing=self._ime_active(), draft=self.state.draft,
            read_matches=self.fallback_mode or (self.last_read is not None
                                                and self.last_read[0] == self.state.draft),
            hwnd=self.accept_hwnd, foreground=int(windows_input.user32.GetForegroundWindow() or 0),
            has_bounds=bool(self.bounds), quiet_seconds=time.monotonic() - self.last_typing_at,
            context_allowed=True)

    def show_suggestion(self, text: str) -> None:
        self.popup.show_text(text, self.bounds, suggest=True, fallback=self.fallback_mode)

    def note_typing(self) -> None:
        # Delivered on the Qt thread after the hook records activity.
        if not self.config.enabled:
            return
        hook = getattr(self, "tab_hook", None)
        if hook:
            hook.ready = False
        self.last_typing_at = time.monotonic()
        self.draft_dirty = True
        self.awaiting_committed_edit = True
        self.text_armed = True
        self.last_activity_kind = "keyboard"
        # A fallback draft is a one-time clipboard snapshot. After any real
        # key it can no longer prove what is in an editor that UIA cannot read.
        self.fallback_mode = False
        self.input_activity_pending = True
        self.monitor.wake()

    def note_mouse_activity(self, x: int | None = None, y: int | None = None) -> None:
        if not self.config.enabled:
            return
        hook = getattr(self, "tab_hook", None)
        if hook:
            hook.ready = False
        self.last_typing_at = time.monotonic()
        self.draft_dirty = True
        self.text_armed = False
        self.last_activity_kind = "mouse"
        self.fallback_mode = False
        self.input_activity_pending = True
        self.monitor.wake()
        self.pending_click_position = (x, y) if x is not None and y is not None else None
        inside_editor = (x is not None and y is not None and self.bounds is not None
                         and self.bounds[0] <= x < self.bounds[2]
                         and self.bounds[1] <= y < self.bounds[3])
        self.resolver.invalidate()
        self.awaiting_editor_click = True
        if not inside_editor:
            self.context_verified = False
            self.context_resolution_state = "waiting"
        # The background UIA monitor must verify the focused editor before
        # starting a task scan; coordinates alone can hit an overlay or stale box.

    def note_navigation(self) -> None:
        self.note_mouse_activity()
        self.last_activity_kind = "navigation"
        self.awaiting_editor_click = False

    def close_tab_hook(self) -> None:
        if self.tab_hook:
            self.tab_hook.close()

    def close_mouse_hook(self) -> None:
        if self.mouse_hook:
            self.mouse_hook.close()

    def tick(self) -> None:
        now = time.monotonic()
        previous_tick = getattr(self, "_previous_tick", now)
        self._previous_tick = now
        if now - previous_tick > .125:
            log_event("main_loop_delay", latency_ms=round((now - previous_tick - .025) * 1000))
        composing = self._ime_active()
        if composing != getattr(self, "ime_composing", False):
            self.ime_composing = composing
            self.last_typing_at = time.monotonic()
            self.draft_dirty = True
            self.invalidate()
            self.monitor.wake()
        if self.input_activity_pending:
            self.input_activity_pending = False
            if (self.last_activity_kind in {"mouse", "navigation"}
                    and self.last_codex_hwnd
                    and windows_input.user32.GetForegroundWindow() != self.last_codex_hwnd):
                self.context_verified = False
            if (self.last_activity_kind != "keyboard"
                    or time.monotonic() - self.last_keyboard_log_at > 0.5):
                log_event("input_activity", kind=self.last_activity_kind,
                          hwnd=int(windows_input.user32.GetForegroundWindow() or 0))
                if self.last_activity_kind == "keyboard":
                    self.last_keyboard_log_at = time.monotonic()
            if self.state.active is not None or self.state.suggestion or self.popup.isVisible():
                self.invalidate()
        if (self.popup.isVisible() and self.accept_hwnd
                and windows_input.user32.GetForegroundWindow() != self.accept_hwnd):
            self.invalidate()
        hook = getattr(self, "tab_hook", None)
        if hook:
            hook.ready = self.can_accept_tab()
        if self.inserting or not self.config.enabled:
            return
        now = time.monotonic()
        if not self.text_armed or not self.state.draft.strip():
            return
        reason = self.editor_snapshot().rejection()
        if reason == "ready":
            reason = ("backend_unavailable" if not self.ready else
                      "already_requested" if not self.state.ready(now) else "ready")
        if reason != getattr(self, "last_request_gate_reason", ""):
            self.last_request_gate_reason = reason
            log_event("request_gate", reason=reason)
        if reason == "ready":
            self.start_request()

    def fallback_draft(self) -> None:
        hwnd = int(windows_input.user32.GetForegroundWindow() or 0)
        draft = windows_input.copy_draft_fallback(self.app.clipboard())
        if hwnd != windows_input.user32.GetForegroundWindow():
            self.invalidate()
            return
        if draft is not None and self.config.enabled:
            if hwnd != self.last_codex_hwnd:
                self.context_verified = False
            self.fallback_mode = True
            self.awaiting_committed_edit = False
            self.text_armed = bool(draft.strip())
            self.last_read = None
            self.draft_dirty = False
            self.bounds = windows_input.foreground_bounds()
            self.accept_hwnd = hwnd
            self.input_activity_pending = False
            self.state.observe(draft, self.context_revision, time.monotonic())
            self.popup.hide()

    def accept_suggestion(self) -> None:
        if not self.can_accept_tab():
            return
        suggestion = self.state.suggestion
        current = self.state.draft
        used_fallback = self.fallback_mode
        self.inserting = True
        try:
            inserted = windows_input.insert_text(
                suggestion, self.app.clipboard(), expected_hwnd=self.accept_hwnd)
        finally:
            self.inserting = False
        if inserted:
            log_event("suggestion_accepted", generation=self.state.generation,
                      suggestion_len=len(suggestion))
            if self.cancel:
                self.cancel.set()
            self.pending_insertion = None if used_fallback else (current, suggestion)
            self.fallback_mode = False
            self.text_armed = False  # Wait for the next user edit before generating again.
            self.awaiting_committed_edit = False
            self.last_typing_at = time.monotonic()
            self.draft_dirty = True
            self.monitor.wake()
            self.state.observe(current + suggestion, self.context_revision, time.monotonic())
            self.popup.hide()
        else:
            self.tray.showMessage(tr("insert_failed"), tr("focus_codex_first"))

    def start_request(self) -> None:
        context = self.completion_context()
        if (not self.backend or not self.ready
                or self.editor_snapshot().rejection() != "ready"
                or not self.state.ready(time.monotonic())):
            return
        token = self.state.start()
        self.request_started_at = time.monotonic()
        log_event("request_started", generation=token.generation,
                  draft_len=len(token.draft), revision=token.context_revision,
                  hwnd=self.accept_hwnd, context_count=len(context),
                  context_chars=sum(len(message.text) for message in context),
                  context_roles=("".join(message.role[0] for message in context)
                                 or "none"))
        request = SuggestionRequest(context, token.draft)
        self.cancel = self.inference.submit(token, request, self.backend)

    def on_finished(self, token: RequestToken, text: str) -> None:
        if self.state.finish(token, text):
            shown = bool(text and self.editor_snapshot().rejection() == "ready")
            if shown:
                self.show_suggestion(text)
                shown = self.popup.isVisible()
                log_event("suggestion_latency", generation=token.generation,
                          latency_ms=round((time.monotonic() - self.last_typing_at) * 1000),
                          shown=self.popup.isVisible())
                hook = getattr(self, "tab_hook", None)
                if hook:
                    hook.ready = self.can_accept_tab()
            else:
                self.popup.hide()
            log_event("request_finished", generation=token.generation,
                      suggestion_len=len(text), shown=shown,
                      reason="shown" if shown else ("empty_suffix" if not text else "snapshot_invalid"),
                      latency_ms=round((time.monotonic() - self.request_started_at) * 1000))
        else:
            log_event("request_discarded", generation=token.generation)

    def on_failed(self, token: RequestToken, message: str) -> None:
        if self.state.finish(token, ""):
            log_event("request_failed", generation=token.generation,
                      latency_ms=round((time.monotonic() - self.request_started_at) * 1000))
            self.popup.hide()
            self.tray.showMessage(tr("completion_failed"), message[:200])


def main() -> int:
    if sys.platform != "win32":
        print("This application requires Windows.", file=sys.stderr)
        return 1
    from .startup import normalize_startup
    relaunched = normalize_startup()
    if relaunched is not None:
        return relaunched
    if "--startup-test" in sys.argv[1:]:
        from .startup_acceptance import main as startup_main
        return startup_main([arg for arg in sys.argv[1:] if arg != "--startup-test"])
    if "--self-test" in sys.argv[1:]:
        from .acceptance import main as acceptance_main
        return acceptance_main([arg for arg in sys.argv[1:] if arg != "--self-test"])
    background = "--background" in sys.argv[1:]
    return run_application(background=background)


def run_application(*, background: bool = False, config: AppConfig | None = None,
                    instance_name: str = "Local\\CodexCue.Main", on_ready=None) -> int:
    """The normal tray lifecycle, also exercised by isolated startup acceptance."""
    instance = _acquire_single_instance(instance_name, notify_existing=not background)
    if instance is None:
        return 0
    kernel32, handle, open_event = instance
    try:
        setup_logging()
        log_event("app_started")
        app = QApplication([arg for arg in sys.argv if arg != "--background"])
        app.setWindowIcon(app_icon())
        app.setQuitOnLastWindowClosed(False)
        first_run = config is None and not existing_config_path().exists()
        config = config if config is not None else AppConfig.load()
        needs_setup = first_run or getattr(config, "recovery_required", False)
        if needs_setup and not SettingsDialog(config).exec():
            return 0
        companion = Companion(app, config)
        # Keep the controller alive for the lifetime of the Qt event loop.
        app._companion = companion
        shortcut_timer = QTimer(app)
        shortcut_timer.timeout.connect(
            lambda: _handle_shortcut_requests(kernel32, open_event, companion))
        shortcut_timer.start(100)
        app._shortcut_timer = shortcut_timer
        if not background and not needs_setup:
            QTimer.singleShot(0, companion.open_settings)
        if on_ready is not None:
            QTimer.singleShot(0, lambda: on_ready(companion))
        return app.exec()
    finally:
        kernel32.CloseHandle(handle)
        kernel32.CloseHandle(open_event)


def _handle_shortcut_requests(kernel32: object, open_event: int, companion: Companion) -> None:
    if kernel32.WaitForSingleObject(open_event, 0) == 0:  # WAIT_OBJECT_0
        companion.open_settings_from_shortcut()


def _acquire_single_instance(name: str = "Local\\CodexCue.Main",
                             notify_existing: bool = True) -> tuple[object, int, int] | None:
    """Keep one tray app per session and forward duplicate launches to it."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateEventW.argtypes = (ctypes.c_void_p, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_wchar_p)
    kernel32.CreateEventW.restype = ctypes.c_void_p
    kernel32.SetEvent.argtypes = (ctypes.c_void_p,)
    kernel32.SetEvent.restype = ctypes.c_int
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint)
    kernel32.WaitForSingleObject.restype = ctypes.c_uint
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_int
    open_event = kernel32.CreateEventW(None, False, False, f"{name}.OpenSettings")
    if not open_event:
        raise ctypes.WinError(ctypes.get_last_error())
    handle = kernel32.CreateMutexW(None, False, name)
    mutex_error = ctypes.get_last_error()
    if not handle:
        kernel32.CloseHandle(open_event)
        raise ctypes.WinError(mutex_error)
    if mutex_error == 183:  # ERROR_ALREADY_EXISTS
        if notify_existing:
            kernel32.SetEvent(open_event)
        kernel32.CloseHandle(handle)
        kernel32.CloseHandle(open_event)
        return None
    return kernel32, handle, open_event
