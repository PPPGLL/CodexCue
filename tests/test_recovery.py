import asyncio
import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from PySide6.QtCore import QRect

from codex_companion.app import InferenceWorker, popup_position
from codex_companion.model import OllamaBackend, SuggestionRequest
from codex_companion.sessions import Message
from codex_companion.state import RequestToken
from test_blank_popup import controller


@pytest.mark.parametrize("stall", ["headers", "body"])
def test_cancel_interrupts_network_and_starts_next_request(stall):
    started, interrupted, finished = (threading.Event() for _ in range(3))
    calls, results = [], []

    class Paused(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            try:
                await asyncio.sleep(60)
                yield b"unused"
            finally:
                interrupted.set()

    async def handler(request):
        calls.append(True)
        if len(calls) == 1:
            if stall == "headers":
                started.set()
                try:
                    await asyncio.sleep(60)
                finally:
                    interrupted.set()
            return httpx.Response(200, stream=Paused())
        raw = json.dumps({"continuation": "second suffix"})
        body = json.dumps({"message": {"content": raw}, "done": True}) + "\n"
        return httpx.Response(200, text=body)

    transport = httpx.MockTransport(handler)
    backend = OllamaBackend("http://127.0.0.1:11434", "test", transport)
    def done(token, text):
        results.append((token.draft, text))
        if token.draft == "second":
            finished.set()
    errors = []
    worker = InferenceWorker(SimpleNamespace(finished=SimpleNamespace(emit=done),
                                             failed=SimpleNamespace(emit=lambda *args: errors.append(args))))
    try:
        worker.submit(RequestToken(1, "first", 1), SuggestionRequest([], "first"), backend)
        assert started.wait(2)
        began = time.monotonic()
        worker.submit(RequestToken(2, "second", 1), SuggestionRequest([], "second"), backend)
        assert finished.wait(1), errors
        assert time.monotonic() - began < .8
        assert interrupted.is_set()
        assert results[-1] == ("second", " suffix")
        assert not errors
    finally:
        worker.stop()
        worker.thread.join(2)
        backend.close()


def test_total_deadline_also_bounds_a_stream_that_keeps_trickling():
    class Trickle(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(.02)
                yield b'{"message":{"content":""}}\n'
    backend = OllamaBackend("http://127.0.0.1:11434", "test",
                             httpx.MockTransport(lambda _: httpx.Response(200, stream=Trickle())),
                             request_timeout=.15)
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="time limit"):
            backend.suggest(SuggestionRequest([], "draft"), lambda _: None, threading.Event())
        assert time.monotonic() - started < .8
    finally:
        backend.close()


def test_release_cancels_warm_and_prevents_late_work_from_reloading():
    started, stopped = threading.Event(), threading.Event()
    payloads = []
    class Warm(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            try:
                await asyncio.sleep(60)
                yield b'{}'
            finally:
                stopped.set()
    def handler(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, stream=Warm()) if request.url.path == "/api/chat" else httpx.Response(200, json={})
    backend = OllamaBackend("http://127.0.0.1:11434", "test", httpx.MockTransport(handler))
    warm = threading.Thread(target=backend.warm)
    try:
        warm.start()
        assert started.wait(2)
        release = backend.release_async()
        release.join(1)
        warm.join(1)
        assert stopped.is_set() and not warm.is_alive() and not release.is_alive()
        backend.warm()  # A delayed startup probe must not undo manual release.
        assert backend.suggest(SuggestionRequest([], "draft"), lambda _: None, threading.Event()) == ""
        assert [p["keep_alive"] for p in payloads if "keep_alive" in p] == [-1, 0]
    finally:
        backend.close()


def test_maximized_fallback_popup_stays_on_screen():
    class Screen:
        def geometry(self): return QRect(0, 0, 1920, 1080)
        def availableGeometry(self): return QRect(0, 0, 1920, 1040)
        def devicePixelRatio(self): return 1
    assert popup_position((0, 0, 1920, 1040), 338, 80, [Screen()]) is None
    point = popup_position((0, 0, 1920, 1040), 338, 80, [Screen()], fallback=True)
    assert Screen().availableGeometry().contains(QRect(point.x(), point.y(), 338, 80))


def test_unidentified_task_never_reuses_previous_history():
    companion = controller()
    companion.context_verified = False
    companion.context_messages = [Message("user", "previous private task")]
    assert companion.completion_context() == []


def test_first_message_can_complete_without_a_session(monkeypatch):
    from codex_companion import windows_input
    companion = controller()
    companion.tailer = None
    companion.context_ready = companion.context_verified = False
    companion.context_messages = [Message("user", "must not leak")]
    companion.backend = object()
    sent = []
    companion.inference = SimpleNamespace(submit=lambda token, request, backend: sent.append(request))
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 123)
    companion.state.observe("Could you", 1, 0)
    companion.start_request()
    assert len(sent) == 1 and sent[0].messages == []


@pytest.mark.parametrize("invalid", ["dirty", "pending", "focus", "ime", "read"])
def test_request_display_and_acceptance_share_the_same_snapshot_guard(monkeypatch, invalid):
    from codex_companion import windows_input
    companion = controller()
    companion.backend = object()
    sent = []
    companion.inference = SimpleNamespace(submit=lambda *args: sent.append(args))
    companion.state.observe("draft", 1, 0)
    companion.fallback_mode = False
    companion.last_read = ("draft", companion.bounds)
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 999 if invalid == "focus" else 123)
    companion.draft_dirty = invalid == "dirty"
    companion.input_activity_pending = invalid == "pending"
    companion.ime_guard = SimpleNamespace(active_for=lambda _: invalid == "ime")
    if invalid == "read": companion.last_read = ("stale draft", companion.bounds)
    companion.start_request()
    assert not sent
    token = companion.state.start()
    companion.on_finished(token, " suffix")
    assert not companion.popup.isVisible()
    companion.popup.show_text(" suffix", companion.bounds, suggest=True)
    assert not companion.can_accept_tab()


def test_continuation_keeps_spaces_midword_and_long_draft_boundary():
    from codex_companion.model import decode_suggestion, draft_anchor, continuation_schema
    for draft, suffix in [("Before testing, please", " check the logs"),
                          ("Please check the configu", "ration file"),
                          ("前面的长草稿" * 100 + "参数是", "3.14"),
                          ("首行\n\t然后请", "检查日志")]:
        anchor = draft_anchor(draft)
        assert decode_suggestion(json.dumps({"continuation": anchor + suffix}), draft) == suffix
        import re
        assert re.fullmatch(continuation_schema(draft)["properties"]["continuation"]["pattern"], anchor + suffix)
    with pytest.raises(ValueError, match="changed the existing draft"):
        decode_suggestion('{"continuation":"rewritten input"}', 'original input')


def test_completed_question_can_continue_the_users_request():
    from codex_companion.model import draft_anchor
    draft = "Why did it fail?"
    suffix = " Please check the logs before changing anything."
    def respond(request):
        raw = json.dumps({"continuation": draft_anchor(draft) + suffix})
        return httpx.Response(200, content=json.dumps({"message": {"content": raw}, "done": True}) + "\n")
    backend = OllamaBackend("http://127.0.0.1:11434", "test", httpx.MockTransport(respond))
    try:
        assert backend.suggest(SuggestionRequest([], draft), lambda _: None, threading.Event()) == suffix
    finally:
        backend.close()


def test_throttled_new_window_cannot_reuse_verified_old_history(monkeypatch):
    from codex_companion import windows_input
    companion = controller()
    companion.last_resolution_attempt_at = time.monotonic()
    monkeypatch.setattr(windows_input.user32, "GetForegroundWindow", lambda: 999)
    companion.note_typing()
    companion.on_observed(1, ("new window draft", companion.bounds), 999, companion.last_typing_at)
    assert not companion.context_verified
    assert companion.completion_context() == []
