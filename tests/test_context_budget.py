"""Synthetic regression coverage for 16K context selection and compaction."""
import json
import threading

import httpx
import pytest

from codex_companion import model
from codex_companion.sessions import CONTEXT_STORAGE_BYTES, Message, SessionTailer
from codex_companion.token_budget import TokenCounter


def record(role, text):
    return {"type": "response_item", "payload": {"type": "message", "role": role,
            "phase": "final_answer" if role == "assistant" else None,
            "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}]}}


def append(path, *records):
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def compact(text):
    return {"type": "compacted", "payload": {"message": text,
            "replacement_history": [record("developer", "PRIVATE_RUNTIME_RULES")],
            "guardian_history": [{"text": "PRIVATE_REVIEW_TRANSCRIPT"}]}}


def byte_vocabulary():
    values = list(range(33, 127)) + list(range(161, 173)) + list(range(174, 256))
    mapping = {v: chr(v) for v in values}
    for i, b in enumerate(b for b in range(256) if b not in values):
        mapping[b] = chr(256 + i)
    return {"tokenizer.ggml.model": "gpt2", "tokenizer.ggml.pre": "qwen2",
            "tokenizer.ggml.tokens": [mapping[b] for b in range(256)] + ["ab", "abab"],
            "tokenizer.ggml.token_type": [1] * 258}


def test_installed_vocabulary_counts_tokens_instead_of_characters():
    counter = TokenCounter.from_model_info(byte_vocabulary())
    assert counter.count("abab") == 1
    assert counter.count("你好😃") == 10
    assert counter.clip("abab hello", 1) == "abab"
    assert "�" not in counter.clip("你好😃", 5, tail=True)
    with pytest.raises(ValueError):
        TokenCounter.from_model_info({"tokenizer.ggml.pre": "unknown"})


def test_prime_and_append_compaction_produce_same_context(tmp_path):
    path = tmp_path / "conversation.jsonl"
    append(path, record("user", "Old request"), record("assistant", "Old answer"))
    live = SessionTailer(path)
    assert live.poll()
    append(path, compact("Earlier decision: use red."), record("user", "改为蓝色，以这次为准。"))
    assert live.poll()
    loaded = SessionTailer(path)
    assert loaded.poll()
    expected = [Message("assistant", "Earlier decision: use red.", "task_summary"),
                Message("user", "改为蓝色，以这次为准。")]
    assert live.context() == loaded.context() == expected
    data = json.loads(model.build_messages(model.SuggestionRequest(expected, "颜色用"))[-1]["content"])
    assert [m["speaker"] for m in data["background"]] == ["task_summary", "user"]
    assert "PRIVATE_" not in json.dumps(data)
    append(path, compact("Latest task state"))
    assert live.poll()
    assert live.context() == [Message("assistant", "Latest task state", "task_summary")]
    # File replacement/truncation must drop the old summary too.
    path.write_text("", encoding="utf-8")
    assert not live.poll()
    assert live.context() == []


def test_missing_or_unknown_summary_falls_back_to_actual_dialogue(tmp_path):
    path = tmp_path / "unsupported.jsonl"
    append(path, record("user", "Only keep this real turn."),
           {"type": "compacted", "payload": {"replacement_history": [record("developer", "PRIVATE")]}})
    tailer = SessionTailer(path)
    assert tailer.poll()
    assert tailer.context() == [Message("user", "Only keep this real turn.")]


def test_newest_invalid_summary_does_not_resurrect_an_older_one(tmp_path):
    path = tmp_path / "invalid.jsonl"
    append(path, record("user", "Already summarized"), compact("STALE"))
    live = SessionTailer(path)
    live.poll()
    append(path, record("user", "Recent correction"), compact(None))
    live.poll()
    tailer = SessionTailer(path)
    assert tailer.poll()
    assert tailer.context() == live.context() == [Message("user", "Recent correction")]


def test_partial_compaction_does_not_change_context_until_complete(tmp_path):
    path = tmp_path / "partial.jsonl"
    append(path, record("user", "最新用户要求"))
    tailer = SessionTailer(path)
    tailer.poll()
    raw = (json.dumps(compact("保存这个任务摘要"), ensure_ascii=False) + "\n").encode()
    with path.open("ab") as f:
        f.write(raw[:60])
    assert not tailer.poll()
    with path.open("ab") as f:
        f.write(raw[60:])
    assert tailer.poll()
    assert tailer.context()[0].text == "保存这个任务摘要"


def test_initial_read_ignores_a_partial_record_larger_than_read_chunk(tmp_path):
    path = tmp_path / "large-partial.jsonl"
    append(path, record("user", "Keep this request."))
    with path.open("ab") as f:
        f.write(b'{"type":"response_item","payload":{"output":"' + b'x' * 400000)
    tailer = SessionTailer(path)
    assert tailer.poll()
    assert tailer.context() == [Message("user", "Keep this request.")]


def test_compaction_finishing_after_initial_read_still_updates_summary(tmp_path):
    path = tmp_path / "partial-summary.jsonl"
    append(path, record("user", "Old request"))
    payload = compact("Latest task summary")
    payload["payload"]["replacement_history"] = [record("developer", "x" * 400000)]
    raw = (json.dumps(payload) + "\n").encode()
    with path.open("ab") as f:
        f.write(raw[:-10])
    tailer = SessionTailer(path)
    tailer.poll()
    with path.open("ab") as f:
        f.write(raw[-10:])
    assert tailer.poll()
    assert tailer.context() == [Message("assistant", "Latest task summary", "task_summary")]


def test_giant_message_obeys_storage_bound_on_prime_and_append(tmp_path):
    path = tmp_path / "giant.jsonl"
    append(path, record("user", "Old request"))
    live = SessionTailer(path)
    live.poll()
    append(path, record("user", "笔记" * CONTEXT_STORAGE_BYTES + " KEEP THIS END"))
    live.poll()
    loaded = SessionTailer(path)
    loaded.poll()
    assert live.context() == loaded.context()
    assert sum(len(m.text.encode()) for m in live.context()) <= CONTEXT_STORAGE_BYTES
    assert live.context()[-1].text.endswith("KEEP THIS END")


def test_full_messages_are_kept_when_they_fit():
    counter = TokenCounter.from_model_info(byte_vocabulary())
    messages = [Message("user", "ab" * 5000 + " Important constraint in the middle. " + "ab" * 5000)]
    request = model.SuggestionRequest(messages, "Please check the conn")
    fitted = model.fit_request(request, counter)
    assert fitted == request  # 20K characters can still fit this tokenizer.
    assert counter.chat_tokens(model.build_messages(fitted)) + model.COMPLETION_TOKENS < model.CONTEXT_TOKENS


def test_short_fact_reuse_does_not_discard_the_latest_correction():
    request = model.SuggestionRequest([Message("user", "背景换成蓝色，其他地方保持原样。")], "背景最终改成")
    calls = []
    async def read(messages, schema, budget):
        calls.append(messages)
        return json.dumps({"continuation": "背景最终改成蓝色，其他地方保持原样。"})
    import asyncio
    assert asyncio.run(model.complete_request(read, request)) == "蓝色，其他地方保持原样。"
    assert len(calls) == 1


@pytest.mark.parametrize("native", [False, True])
def test_oversized_history_keeps_correction_summary_and_original_order(native):
    messages = [Message("assistant", "Earlier task goal. " + "old detail " * 9000, "task_summary"),
                Message("user", "outdated notes " * 3000),
                Message("user", "Correction: use blue; do not edit files."),
                Message("assistant", "long answer " * 5000 + "CURRENT ANSWER END")]
    counter = TokenCounter()
    request = model.SuggestionRequest(messages, "The color should be")
    fitted = model.fit_request(request, counter, native=native)
    assert fitted.draft == request.draft
    assert fitted.messages[0].kind == "task_summary"
    assert fitted.messages[0].text.startswith("Earlier task goal.")
    assert Message("user", "Correction: use blue; do not edit files.") in fitted.messages
    assert fitted.messages[-1].text.endswith("CURRENT ANSWER END")
    size = counter.count(model.build_native_prompt(fitted)) + 128 if native else counter.chat_tokens(model.build_messages(fitted))
    assert size + (model.NATIVE_TOKENS if native else model.COMPLETION_TOKENS) <= model.CONTEXT_TOKENS
    # Selection is deterministic across adjacent edits, enabling prefix reuse.
    assert model.fit_request(request, counter, native=native) == fitted
    edited = model.fit_request(model.SuggestionRequest(messages, "So the color should be"), counter, native=native)
    assert edited.messages == fitted.messages
    assert edited.draft == "So the color should be"
    assert len(messages[-1].text) > len(fitted.messages[-1].text)


def test_escaped_paste_cannot_overflow_prompt_budget():
    request = model.SuggestionRequest([Message("user", "Keep this constraint.")], "\x00" * 30000 + " draft")
    fitted = model.fit_request(request, TokenCounter())
    assert fitted.draft.endswith(" draft")
    assert TokenCounter().chat_tokens(model.build_messages(fitted)) + model.COMPLETION_TOKENS < model.CONTEXT_TOKENS


def test_warm_loads_tokenizer_once_and_typing_only_calls_chat():
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/show":
            assert json.loads(request.content)["verbose"] is True
            return httpx.Response(200, json={"model_info": byte_vocabulary()})
        payload = json.loads(request.content)
        assert payload["options"]["num_ctx"] == 16384
        data = json.loads(payload["messages"][-1]["content"])
        return httpx.Response(200, json={"done": True, "message": {
            "content": json.dumps({"continuation": data["anchor"] + " the logs."})}})
    backend = model.OllamaBackend("http://127.0.0.1:11434", "fixture", httpx.MockTransport(handler))
    try:
        backend.warm()
        assert backend.token_counter.encoding is not None
        for draft in ("Please inspect", "Please check"):
            assert backend.suggest(model.SuggestionRequest([], draft), lambda _: None, threading.Event()) == " the logs."
        backend.warm()
        assert calls == ["/api/show", "/api/chat", "/api/chat", "/api/chat", "/api/chat"]
    finally:
        backend.close()
