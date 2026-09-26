import httpx
import threading
import time
from PySide6.QtWidgets import QApplication

from codex_companion.app import SettingsDialog
from codex_companion.config import AppConfig, OLLAMA_MODEL_CHOICES
from codex_companion.model import OllamaBackend


def wait_for(app, condition, timeout=2):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert condition()


def test_settings_offer_presets_installed_models_and_custom_name(monkeypatch):
    from codex_companion import i18n

    monkeypatch.setattr(i18n, "system_ui_languages", lambda: ["zh-CN"])
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(OllamaBackend, "list_models",
                        lambda self: [("qwen3:4b-instruct", 2_500_000_000),
                                      ("other:latest", 1_000_000_000)])
    dialog = SettingsDialog(AppConfig())
    try:
        assert dialog.ollama_model.isEditable()
        assert all(dialog.ollama_model.findText(name) >= 0 for name, _ in OLLAMA_MODEL_CHOICES)
        dialog.check_ollama()
        wait_for(app, lambda: dialog.ollama_model.findText("other:latest") >= 0)
        assert dialog.ollama_model.currentText() == "qwen3:4b-instruct"
        assert "所选模型已安装" in dialog.status.text()
        dialog.ollama_model.setCurrentText("custom-model:latest")
        assert "自定义" in dialog.model_note.text()
        dialog.check_ollama()
        wait_for(app, lambda: "尚未安装" in dialog.status.text())
        assert "尚未安装" in dialog.status.text()
    finally:
        dialog.close()
        app.processEvents()


def test_model_list_ignores_invalid_size_values():
    def handler(request):
        return httpx.Response(200, json={"models": [
            {"name": "missing"}, {"name": "null", "size": None},
            {"name": "wrong", "size": "large"}, {"name": "normal", "size": 1024},
        ]})

    backend = OllamaBackend("http://127.0.0.1:11434", "normal", httpx.MockTransport(handler))
    try:
        assert backend.list_models() == [
            ("missing", 0), ("null", 0), ("wrong", 0), ("normal", 1024)]
    finally:
        backend.close()


def test_settings_require_installed_model_before_saving(monkeypatch):
    from codex_companion import i18n

    monkeypatch.setattr(i18n, "system_ui_languages", lambda: ["zh-CN"])
    app = QApplication.instance() or QApplication([])
    config = AppConfig()
    saved = []
    monkeypatch.setattr(AppConfig, "save", lambda self: saved.append(True))
    gate = threading.Event()
    installed = [False]

    def available(_self):
        gate.wait(2)
        return True, installed[0]

    monkeypatch.setattr(OllamaBackend, "available", available)
    dialog = SettingsDialog(config)
    try:
        dialog.ollama_model.setCurrentText("qwen3:1.7b")
        start = time.monotonic()
        dialog.save()
        assert time.monotonic() - start < 0.1
        assert not dialog.save_button.isEnabled()
        gate.set()
        wait_for(app, lambda: dialog.save_button.isEnabled())
        assert "尚未安装" in dialog.status.text()
        assert config.ollama_model == "qwen3:4b-instruct"
        assert saved == []
        installed[0] = True
        dialog.save()
        wait_for(app, lambda: saved == [True])
        assert config.ollama_model == "qwen3:1.7b"
        assert saved == [True]
    finally:
        dialog.close()
        app.processEvents()
