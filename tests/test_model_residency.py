import json
import threading
from types import SimpleNamespace

import httpx
import pytest
from PySide6.QtWidgets import QMenu

from codex_companion.app import Bridge, Companion
from codex_companion.config import AppConfig
from codex_companion.i18n import tr
from codex_companion.model import OllamaBackend, SuggestionRequest
from test_blank_popup import controller


def managed_controller(handler):
    companion = controller()
    companion.config = AppConfig(ollama_model="fixture:1")
    companion.config.save = lambda: None
    companion.bridge = Bridge()
    companion.bridge.model_released.connect(companion.on_model_released)
    companion.bridge.warmed.connect(companion.on_warmed)
    companion.backend = OllamaBackend("http://127.0.0.1:11434", "fixture:1", httpx.MockTransport(handler))
    companion.menu = QMenu()
    companion.active_context_label = ""
    companion.tray = SimpleNamespace(setToolTip=lambda _: None, setIcon=lambda _: None,
                                    showMessage=lambda *args: None)
    companion._tray_icons = {state: None for state in ("loading", "paused", "ready", "error")}
    companion.refresh_menu = Companion.refresh_menu.__get__(companion, Companion)
    return companion


def test_warm_and_each_completion_keep_model_loaded():
    payloads = []
    def handler(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        data = json.loads(payload["messages"][-1]["content"])
        content = json.dumps({"continuation": data["anchor"] + " the logs."})
        return httpx.Response(200, json={"message": {"content": content}, "done": True})
    backend = OllamaBackend("http://127.0.0.1:11434", "fixture:1", httpx.MockTransport(handler))
    try:
        backend.warm()
        for draft in ("Please inspect", "Please check"):
            assert backend.suggest(SuggestionRequest([], draft), lambda _: None, threading.Event()) == " the logs."
        assert len(payloads) == 3
        assert all(p["keep_alive"] == -1 for p in payloads)
    finally:
        backend.close()


def test_pause_and_resume_do_not_unload_or_rewarm():
    companion = managed_controller(lambda _: pytest.fail("Pause must not contact Ollama"))
    companion.configure_backend = lambda: pytest.fail("Resident model must not be reloaded on resume")
    try:
        companion.toggle()
        assert not companion.config.enabled and companion.ready
        companion.toggle()
        assert companion.config.enabled and companion.ready
    finally:
        companion.backend.close()


def test_tray_release_is_async_and_next_settled_edit_reloads(qtbot, monkeypatch):
    from codex_companion import windows_input
    started, finish = threading.Event(), threading.Event()
    payloads = []
    def handler(request):
        payloads.append(json.loads(request.content))
        started.set()
        assert finish.wait(2)
        return httpx.Response(200, json={})
    companion = managed_controller(handler)
    old = companion.backend
    companion.state.observe("Please inspect", 1, 0)
    token = companion.state.start()
    companion.cancel = threading.Event()
    companion.popup.show_text(" stale result", companion.bounds)
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 123)
    configured = []
    companion.configure_backend = lambda: configured.append(True)
    try:
        companion.refresh_menu()
        action = next(a for a in companion.menu.actions() if a.text() == tr("release_model"))
        assert action.isEnabled()
        action.trigger()
        assert started.wait(1)  # The UI action already returned while HTTP is blocked.
        assert companion.cancel.is_set() and not companion.popup.isVisible()
        assert not companion.ready and not companion.text_armed
        assert companion.releasing_backend is old and companion.backend is None
        companion.on_warmed(old, True, "")
        companion.on_finished(token, " stale result")
        assert not companion.ready and not companion.popup.isVisible()
        assert not next(a for a in companion.menu.actions() if a.text() == tr("release_model")).isEnabled()
        companion.release_model()  # Repeated clicks do not create another unload.
        companion.tick()
        assert not configured
        finish.set()
        qtbot.waitUntil(lambda: companion.releasing_backend is None)
        assert companion.model_released and not companion.backend_error
        assert any(a.text() == tr("model_released") for a in companion.menu.actions())
        companion.tick()
        assert not configured  # No automatic reload after the callback.
        companion.text_armed = True
        companion.draft_dirty = True
        companion.tick()
        assert not configured  # Wait for the updated draft before loading.
        companion.draft_dirty = False
        companion.tick()
        assert configured == [True]
        assert payloads == [{"model": "fixture:1", "keep_alive": 0}]
    finally:
        finish.set()
        if companion._model_release_thread:
            companion._model_release_thread.join(2)
        old.close()


def test_failed_release_is_reported_and_can_reload_on_next_input(qtbot):
    companion = managed_controller(lambda _: httpx.Response(503))
    messages = []
    companion.tray.showMessage = lambda *args: messages.append(args)
    companion.release_model()
    qtbot.waitUntil(lambda: companion.releasing_backend is None)
    assert companion.model_released and not companion.ready
    assert companion.backend_error == tr("model_release_failed")
    assert messages == [(tr("release_model"), tr("model_release_failed"))]
    assert not any(a.text() == tr("model_released") for a in companion.menu.actions())


def test_settings_wait_for_pending_release_before_loading(qtbot, monkeypatch):
    started, finish = threading.Event(), threading.Event()
    def handler(_):
        started.set()
        assert finish.wait(2)
        return httpx.Response(200, json={})
    companion = managed_controller(handler)
    new = SimpleNamespace(close=lambda: None)
    created = []
    monkeypatch.setattr("codex_companion.app.make_backend", lambda cfg: created.append(cfg.ollama_model) or new)
    try:
        companion.release_model()
        assert started.wait(1)
        companion.config.ollama_model = "other:1"
        companion.configure_backend()
        assert not created and companion._backend_reconfigure_pending
        finish.set()
        qtbot.waitUntil(lambda: companion.backend is new)
        assert created == ["other:1"] and not companion.model_released
    finally:
        finish.set()
        companion._model_release_thread.join(2)


def test_switching_back_to_previous_model_waits_for_its_release(qtbot, monkeypatch):
    started, finish = threading.Event(), threading.Event()
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/generate":
            started.set()
            assert finish.wait(2)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "fixture:1"}]})
        return httpx.Response(200, json={})
    companion = managed_controller(handler)
    old = companion.backend
    monkeypatch.setattr("codex_companion.app.make_backend", lambda cfg:
                        OllamaBackend(cfg.ollama_url, cfg.ollama_model, httpx.MockTransport(handler)))
    try:
        companion.config.ollama_model = "other:1"
        companion.configure_backend()
        assert started.wait(1)
        companion.config.ollama_model = "fixture:1"
        companion.configure_backend()
        assert calls == ["/api/generate"] and not companion.ready
        finish.set()
        qtbot.waitUntil(lambda: companion.ready)
        assert calls == ["/api/generate", "/api/tags", "/api/chat"]
        assert companion.backend is not old and companion.backend.model == "fixture:1"
    finally:
        finish.set()
        companion._model_release_thread.join(2)
        if companion.backend:
            companion.backend.close()
