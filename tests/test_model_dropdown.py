from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from codex_companion.app import SettingsDialog
from codex_companion.config import AppConfig


def test_rapid_dropdown_reopen_is_immediate_and_keeps_selection(qapp, qtbot):
    """Reopen inside Qt's 150 ms roll animation, then select a real item."""
    previous = QApplication.isEffectEnabled(Qt.UI_AnimateCombo)
    QApplication.setEffectEnabled(Qt.UI_AnimateCombo, True)
    dialog = SettingsDialog(AppConfig())
    qtbot.addWidget(dialog)
    combo = dialog.ollama_model
    original = combo.currentText()
    dialog.show()
    qtbot.waitExposed(dialog)
    dialog.activateWindow()
    qtbot.waitActive(dialog)
    # Let native window activation settle before timing popup reopen events.
    # Otherwise a queued activation change can close the first popup on Windows.
    qtbot.wait(100)
    try:
        for delay in (0, 10, 30, 70, 160) * 4:
            qtbot.mouseClick(combo, Qt.LeftButton,
                             pos=QPoint(combo.width() - 18, combo.height() // 2))
            popup = combo.view().window()
            assert popup.isVisible()
            # The real list must appear immediately, not a separate animated
            # snapshot that can outlive a hide/reopen and retain stale pixels.
            assert not any(w.metaObject().className() == "QRollEffect"
                           and w.isVisible() for w in QApplication.topLevelWidgets())
            qtbot.wait(delay)
            assert popup.isVisible()
            qtbot.keyClick(combo.view(), Qt.Key_Escape)
            assert not popup.isVisible()
            assert combo.currentText() == original

        combo.showPopup()
        qtbot.keyClick(combo.view(), Qt.Key_Down)
        qtbot.keyClick(combo.view(), Qt.Key_Return)
        assert not combo.view().window().isVisible()
        assert combo.currentText() != original
        assert QApplication.isEffectEnabled(Qt.UI_AnimateCombo)
    finally:
        combo.hidePopup()
        dialog.close()
        QApplication.setEffectEnabled(Qt.UI_AnimateCombo, previous)
