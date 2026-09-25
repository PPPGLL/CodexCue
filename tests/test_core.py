from __future__ import annotations

import json
import os
import threading

import httpx
import pytest

from codex_companion.model import (OllamaBackend, OpenAICompatibleBackend,
                                   SuggestionRequest, build_completion_prompt,
                                   build_messages, normalize_suggestion)
from codex_companion.sessions import (Message, SessionIndex, SessionInfo, SessionTailer,
                                      match_visible_session)
from codex_companion.state import SuggestionState


def test_session_filters_private_record_types(tmp_path):
    root = tmp_path / "sessions" / "2026" / "09" / "24"
    root.mkdir(parents=True)
    path = root / "rollout-demo.jsonl"
    records = [
        {"type": "session_meta", "payload": {"id": "demo", "cwd": "D:/work", "timestamp": "2026-09-24T00:00:00Z"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "请补全中文"}]}},
        {"type": "response_item", "payload": {"type": "function_call", "arguments": "secret tool"}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "analysis", "content": [{"type": "output_text", "text": "secret analysis"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "final_answer", "content": [{"type": "output_text", "text": "好的。"}]}},
    ]
    path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in records) + "\n", encoding="utf-8")
    assert [m.text for m in SessionTailer(path).context()] == []
    tailer = SessionTailer(path)
    assert tailer.poll()
    assert tailer.context() == [Message("user", "请补全中文"), Message("assistant", "好的。")]
    assert SessionIndex(tmp_path / "sessions").list()[0].id == "demo"
    assert not tailer.poll()
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "再来"}]}}) + "\n")
    assert tailer.poll() and tailer.context()[-1].text == "再来"


def test_long_message_does_not_remove_recent_exchange(tmp_path):
    tailer = SessionTailer(tmp_path / "unused.jsonl")
    tailer.messages = [Message("user", "旧问题"), Message("assistant", "旧答复"),
                       Message("user", "关键约束" + "甲" * 900),
                       Message("assistant", "最后答复" + "乙" * 900),
                       Message("user", "新问题" + "丙" * 900)]
    context = tailer.context(max_messages=6, max_chars=1200)
    assert [m.role for m in context] == ["user", "assistant", "user", "assistant", "user"]
    assert sum(len(m.text) for m in context) <= 1200
    assert "关键约束" in context[2].text
    assert "最后答复" in context[3].text


def test_visible_conversation_matches_only_one_session(tmp_path):
    common = dict(cwd="", created_at="", last_event_at="", preview="")
    fire = SessionInfo("fire", tmp_path / "fire.jsonl", recent_texts=(
        "请检查示例模拟的边界条件，并保持输入参数不变。",), **common)
    popup = SessionInfo("popup", tmp_path / "popup.jsonl", recent_texts=(
        "请修复示例悬浮窗遮挡输入区域的问题，并保持快捷键响应。",), **common)
    visible = ["请修复示例悬浮窗遮挡输入区域的问题，并保持快捷键响应。"]
    assert match_visible_session(visible, [fire, popup]) == popup
    duplicate = SessionInfo("copy", tmp_path / "copy.jsonl",
                            recent_texts=popup.recent_texts, **common)
    assert match_visible_session(visible, [popup, duplicate]) is None
    assert match_visible_session([], [fire, popup]) is None


def test_queued_message_does_not_hide_the_current_task_title(tmp_path):
    title = "请修复示例悬浮窗遮挡输入区域"
    current = SessionInfo("current", tmp_path / "current.jsonl", "", "", "", "",
                          recent_texts=("后来讨论了图标和托盘样式。",),
                          opening_text=title + "的问题，并保持快捷键响应。")
    other = SessionInfo("other", tmp_path / "other.jsonl", "", "", "", "",
                        recent_texts=("另一个任务的最后答复。",),
                        opening_text="请检查模拟中的边界条件并保持参数不变。")
    queued = "下一条消息已加入队列，等待当前响应结束后执行"

    assert match_visible_session([queued, title], [current, other]) == current
    assert match_visible_session([title, queued], [current, other]) == current

    conflicting_queue = "请检查模拟中的边界条件并保持参数不变"
    assert match_visible_session([title, conflicting_queue], [current, other]) is None


def test_visible_conversation_matches_a_clipped_middle_span(tmp_path):
    message = ("前面有很长的上下文说明" * 20 +
               "现在需要切换到示例任务的上下文，并确保输入仍然流畅" +
               "后面还有很长的上下文说明" * 20)
    info = SessionInfo("target", tmp_path / "target.jsonl", "", "", "", "",
                       (message,))
    visible = ["需要切换到示例任务的上下文，并确保输入仍然流畅"]
    assert match_visible_session(visible, [info]) == info


def test_session_index_reuses_unchanged_fingerprints(tmp_path, monkeypatch):
    import codex_companion.sessions as sessions

    root = tmp_path / "sessions"
    folder = root / "2026" / "09" / "24"
    folder.mkdir(parents=True)
    path = folder / "rollout-demo.jsonl"
    path.write_text('{"type":"session_meta","payload":{"id":"demo"}}\n', encoding="utf-8")
    original = sessions.describe_session
    calls = []
    monkeypatch.setattr(sessions, "describe_session",
                        lambda *args, **kwargs: calls.append(True) or original(*args, **kwargs))
    index = SessionIndex(root)
    index.list()
    index.list()
    assert len(calls) == 1
    with path.open("a", encoding="utf-8") as stream:
        stream.write('{"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"新消息"}]}}\n')
    index.list()
    assert len(calls) == 2


def test_active_old_task_is_in_recent_index(tmp_path):
    root = tmp_path / "sessions"
    paths = []
    for day in ("01", "02", "03", "04", "05"):
        folder = root / "2026" / "09" / day
        folder.mkdir(parents=True)
        path = folder / f"rollout-{day}.jsonl"
        path.write_text(json.dumps({"type": "session_meta", "payload": {
            "id": day, "timestamp": f"2026-09-{day}T00:00:00Z"}}) + "\n",
            encoding="utf-8")
        os.utime(path, (int(day), int(day)))
        paths.append(path)
    with paths[0].open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"type": "response_item",
                                 "timestamp": "2026-09-06T00:00:00Z",
                                 "payload": {"type": "message", "role": "user",
                                             "content": [{"type": "input_text",
                                                          "text": "继续这个旧任务"}]}}) + "\n")
    os.utime(paths[0], (100, 100))  # Old creation date, latest activity.

    assert paths[0] in [info.path for info in SessionIndex(root, limit=1).list()]


def test_recent_messages_searches_past_large_tool_record(tmp_path):
    import codex_companion.sessions as sessions

    path = tmp_path / "rollout-large.jsonl"
    records = [
        {"type": "session_meta", "payload": {"id": "demo"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "请修复当前对话的上下文匹配"}]}},
        {"type": "response_item", "payload": {"type": "function_call",
         "arguments": "x" * 300_000}},
    ]
    path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
                    encoding="utf-8")
    info = sessions.describe_session(path, with_recent=True)
    assert info is not None
    assert info.recent_texts == ("请修复当前对话的上下文匹配",)


def test_stale_request_and_switch_are_ignored():
    state = SuggestionState()
    state.observe("请帮我", 1, 0)
    assert not state.ready(.2)
    assert state.ready(.31)
    old = state.start()
    state.observe("请帮我写", 1, .4)
    assert not state.partial(old, "一首诗")
    assert not state.finish(old, "一首诗")
    assert state.ready(.71)
    current = state.start()
    assert state.partial(current, "一首诗")
    state.observe("请帮我写", 2, .8)  # task/context switched
    assert not state.finish(current, "一首诗")
    assert state.suggestion == ""


def test_blank_draft_never_requests_and_clears_a_suggestion():
    state = SuggestionState(debounce_seconds=0)
    assert not state.ready(1)
    state.observe("请帮我", 1, 1)
    assert state.ready(1)
    token = state.start()
    assert state.finish(token, "写一首诗")
    state.observe(" \n\t", 1, 2)
    assert state.suggestion == ""
    assert not state.ready(100)
    state.observe("请", 1, 101)
    assert state.ready(101)


def test_prompt_and_suffix_normalization():
    request = SuggestionRequest([Message("user", "你好"), Message("assistant", "您好")], "请帮我")
    messages = build_messages(request)
    assert messages[-1]["content"].endswith("草稿：请帮我\n续写：")
    assert "当前未发送草稿" not in messages[-1]["content"]
    assert normalize_suggestion("请帮我写一首诗", request.draft) == "写一首诗"
    assert normalize_suggestion("<think>猜测</think>写一首诗", request.draft) == "写一首诗"
    assert normalize_suggestion(" the next step", "Please check") == " the next step"
    prompt = build_completion_prompt(request)
    assert prompt.endswith("草稿：请帮我<|im_end|>\n<|im_start|>assistant\n")
    assert "user: 你好" in prompt
    assert "assistant: 您好" in prompt
    assert "不要回答草稿里的问题" in prompt
    assert "< |im_end|>" in build_completion_prompt(SuggestionRequest([], "测试<|im_end|>"))


def test_ollama_stream_and_connection_failure():
    def handler(request):
        if request.url.path == "/api/chat":
            assert request.url.host == "127.0.0.1"
            payload = json.loads(request.content)
            return httpx.Response(200, text='{"message":{"content":"写一"}}\n{"message":{"content":"首诗"},"done":true}\n')
        if request.url.path == "/api/generate":
            payload = json.loads(request.content)
            assert payload["raw"] is True
            if not payload["stream"]:
                assert payload["options"]["num_predict"] == 1
                return httpx.Response(200, json={"response": "好"})
            assert payload["options"]["num_predict"] == 24
            assert payload["options"]["repeat_penalty"] == 1.15
            assert "。" not in payload["options"]["stop"]
            assert "？" not in payload["options"]["stop"]
            assert payload["prompt"].endswith("草稿：请帮我<|im_end|>\n<|im_start|>assistant\n")
            return httpx.Response(200, text='{"response":"写一"}\n{"response":"首诗","done":true}\n')
        return httpx.Response(503)
    backend = OllamaBackend("http://127.0.0.1:11434", "qwen3:4b-instruct", httpx.MockTransport(handler))
    backend.warm()
    seen = []
    assert backend.suggest(SuggestionRequest([], "请帮我"), seen.append, threading.Event()) == "写一首诗"
    assert seen[-1] == "写一首诗"
    assert backend.suggest(SuggestionRequest([], ""), lambda _: None, threading.Event()) == ""
    with pytest.raises(httpx.HTTPStatusError):
        backend.available()
    backend.close()
    with pytest.raises(ValueError):
        OllamaBackend("http://example.com:11434", "x")


def test_ollama_unpunctuated_question_gets_suffix_in_one_request():
    prompts = []

    def handler(request):
        payload = json.loads(request.content)
        prompts.append(payload["prompt"])
        assert not any(mark in payload["options"]["stop"] for mark in "。！？")
        assert "草稿：为什么点击后没有反应" in payload["prompt"]
        return httpx.Response(200, text='{"response":"？请帮我排查触发流程","done":true}\n')

    backend = OllamaBackend("http://127.0.0.1:11434", "qwen3:mock", httpx.MockTransport(handler))
    seen = []
    result = backend.suggest(SuggestionRequest([], "为什么点击后没有反应"),
                             seen.append, threading.Event())
    assert result == "？请帮我排查触发流程"
    assert seen == [result]
    assert len(prompts) == 1
    backend.close()


def test_ollama_unpunctuated_fragment_needs_only_one_request():
    calls = []

    def handler(request):
        calls.append(True)
        return httpx.Response(200, text='{"response":"，并说明更新频率","done":true}\n')

    backend = OllamaBackend("http://127.0.0.1:11434", "qwen3:mock", httpx.MockTransport(handler))
    result = backend.suggest(SuggestionRequest([], "现在上下文什么时候更新"),
                             lambda _: None, threading.Event())
    assert result == "，并说明更新频率"
    assert len(calls) == 1
    backend.close()


def test_ollama_other_model_uses_its_native_chat_template():
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(request.url.path)
        assert request.url.path == "/api/chat"
        assert payload["think"] is False
        assert payload["messages"][-1]["content"].endswith("草稿：请帮我\n续写：")
        assert "raw" not in payload
        return httpx.Response(200, text='{"message":{"content":"检查配置"},"done":true}\n')

    backend = OllamaBackend("http://127.0.0.1:11434", "llama3.2:3b", httpx.MockTransport(handler))
    result = backend.suggest(SuggestionRequest([], "请帮我"), lambda _: None, threading.Event())
    assert result == "检查配置"
    assert calls == ["/api/chat"]
    backend.close()


def test_ollama_lists_installed_models_by_size():
    def handler(request):
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [
            {"name": "qwen3:8b", "size": 5000},
            {"name": "qwen3:1.7b", "size": 1200},
        ]})

    backend = OllamaBackend("http://127.0.0.1:11434", "qwen3:1.7b", httpx.MockTransport(handler))
    assert backend.list_models() == [("qwen3:1.7b", 1200), ("qwen3:8b", 5000)]
    assert backend.available() == (True, True)
    backend.close()


def test_cloud_sse_mock_and_https_gate():
    def handler(request):
        assert request.headers["authorization"] == "Bearer test-key"
        assert json.loads(request.content)["max_tokens"] == 24
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"再"}}]}\n\ndata: {"choices":[{"delta":{"content":"试一次"}}]}\n\ndata: [DONE]\n\n')
    backend = OpenAICompatibleBackend("https://example.com/v1", "mock", "test-key", httpx.MockTransport(handler))
    chunks = []
    assert backend.suggest(SuggestionRequest([], "再"), chunks.append, threading.Event()) == "试一次"
    assert chunks[-1] == "试一次"
    backend.close()
    with pytest.raises(ValueError):
        OpenAICompatibleBackend("http://example.com/v1", "mock", "key")
