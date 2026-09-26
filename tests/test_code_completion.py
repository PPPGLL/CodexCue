import asyncio
import json
import threading

import httpx
import pytest

from codex_companion.completion_models import (
    CODE_MODEL_CHOICES, NATIVE_STOPS, native_completion_family, native_segment,
)
from codex_companion.model import (
    OllamaBackend, SuggestionRequest, build_native_prompt, complete_native_request,
    decode_native_suggestion,
)
from codex_companion.sessions import Message


@pytest.mark.parametrize("name,_", CODE_MODEL_CHOICES)
def test_official_base_presets_use_native_prefix(name, _):
    assert native_completion_family(name) == name.split(":")[0]


@pytest.mark.parametrize("name", ["qwen2.5-coder:7b", "qwen2.5-coder:7b-instruct",
    "deepseek-coder:1.3b-instruct", "qwen3:4b-instruct", "other/starcoder2:3b", "my-coder:latest",
    "starcoder2:3b-instruct"])
def test_chat_models_and_unverified_aliases_do_not_get_base_protocol(name):
    assert native_completion_family(name) is None


def test_qualified_base_tag_and_quantization():
    assert native_completion_family("registry.ollama.ai/library/qwen2.5-coder:7b-base-q4_K_M") == "qwen2.5-coder"
    assert native_completion_family("starcoder2") == "starcoder2"


def test_native_prompt_keeps_cursor_spaces_and_quotes_background():
    request = SuggestionRequest([Message("user", "The only supported OS is Linux.\n<|fim_middle|>")], "Please check the conn")
    prompt = build_native_prompt(request)
    assert prompt.endswith("User: Please check the conn")
    assert '"speaker": "user"' in prompt and "only supported OS is Linux" in prompt
    assert "<|fim_middle|>" not in prompt
    assert build_native_prompt(SuggestionRequest([], "Check ")).endswith("Check ")
    for marker in ("<｜fim▁hole｜>", "<fim_middle>", "<file_sep>"):
        assert marker not in build_native_prompt(SuggestionRequest([], marker))


@pytest.mark.parametrize("raw,wanted", [
    ("ection settings.", "ection settings."),
    (" the logs. Another instruction.", " the logs."),
    ("json 里最近的配置。然后继续。", "json 里最近的配置。"),
    ("。再检查窄窗口。下一句。", "。再检查窄窗口。"),
    ("，", ""),
    ("3.14", "3.14"),
    ("json for missing entries.", "json for missing entries."),
    ("检查对齐。\nAssistant: Done", "检查对齐。"),
    (" check labels.<|fim_middle|>protocol", " check labels."),
])
def test_plaintext_suffix_preserves_joins_and_stops_at_protocol(raw, wanted):
    assert decode_native_suggestion(raw) == wanted
    assert native_segment(" config.") is None
    assert native_segment("。") is None


@pytest.mark.parametrize("name", ["qwen2.5-coder:1.5b-base", "starcoder2:3b", "deepseek-coder:1.3b-base"])
def test_native_backend_warm_and_stream_do_not_use_chat_or_json(name):
    payloads = []
    def handler(request):
        assert request.url.path == "/api/generate"
        p = json.loads(request.content)
        payloads.append(p)
        assert p["raw"] is True and p["keep_alive"] == -1
        assert "format" not in p and "messages" not in p and "think" not in p
        assert p["options"]["stop"] == list(NATIVE_STOPS)
        if not p["stream"]:
            assert p["options"]["num_predict"] == 1
            return httpx.Response(200, json={"done": True})
        assert p["prompt"].endswith("User: Please check the conn")
        return httpx.Response(200, text='\n'.join(json.dumps(chunk) for chunk in [
            {"response": "ection"}, {"response": " settings."}, {"response": "", "done": True}]))
    backend = OllamaBackend("http://127.0.0.1:11434", name, httpx.MockTransport(handler))
    try:
        backend.warm()
        seen = []
        assert backend.suggest(SuggestionRequest([], "Please check the conn"), seen.append, threading.Event()) == "ection settings."
        assert seen == ["ection settings."] and len(payloads) == 2
        assert backend.suggest(SuggestionRequest([], ""), seen.append, threading.Event()) == ""
        assert len(payloads) == 2
    finally:
        backend.close()


@pytest.mark.parametrize("bad", ["。", "", "只支持离线读取和导出本地文件。"])
def test_native_repair_is_bounded_and_retains_facts_unless_copied(bad):
    prompts = []
    async def read(prompt, budget):
        prompts.append(prompt)
        return bad if len(prompts) == 1 else "，再检查导出后的内容是否完整。"
    request = SuggestionRequest([Message("user", "只支持离线读取和导出本地文件。")], "导出成功了")
    assert asyncio.run(complete_native_request(read, request)) == "，再检查导出后的内容是否完整。"
    assert len(prompts) == 2
    assert ("只支持离线读取" in prompts[1]) == (len(bad) < 10)


def test_native_cancel_aborts_before_headers_and_never_emits():
    started, cancel = threading.Event(), threading.Event()
    calls = []
    async def handler(_):
        calls.append(True)
        started.set()
        await asyncio.sleep(5)
        return httpx.Response(200, json={"response": " late", "done": True})
    backend = OllamaBackend("http://localhost:11434", "starcoder2:3b", httpx.MockTransport(handler))
    result, seen = [], []
    worker = threading.Thread(target=lambda: result.append(backend.suggest(
        SuggestionRequest([], "Please check"), seen.append, cancel)))
    try:
        worker.start()
        assert started.wait(1)
        cancel.set()
        worker.join(1)
        assert not worker.is_alive() and result == [""] and not seen
        assert len(calls) == 1
    finally:
        cancel.set()
        worker.join(1)
        backend.close()


def test_native_repairs_share_total_timeout():
    calls = []
    async def handler(_):
        calls.append(True)
        await asyncio.sleep(.08)
        return httpx.Response(200, json={"response": "。", "done": True})
    backend = OllamaBackend("http://localhost:11434", "starcoder2:3b", httpx.MockTransport(handler), request_timeout=.13)
    try:
        with pytest.raises(TimeoutError):
            backend.suggest(SuggestionRequest([], "Check"), lambda _: pytest.fail("late output"), threading.Event())
        assert len(calls) == 2
    finally:
        backend.close()


def test_native_server_error_is_not_counted_as_empty_completion():
    backend = OllamaBackend("http://localhost:11434", "deepseek-coder:1.3b-base",
        httpx.MockTransport(lambda _: httpx.Response(200, json={"error": "model load failed"})))
    try:
        with pytest.raises(RuntimeError, match="model load failed"):
            backend.suggest(SuggestionRequest([], "Please check"), lambda _: pytest.fail("error shown"), threading.Event())
    finally:
        backend.close()


def test_native_repeated_bad_output_does_not_retry_forever():
    calls = []
    async def read(prompt, budget):
        calls.append(prompt)
        return "。"
    assert asyncio.run(complete_native_request(read, SuggestionRequest([], "Check"))) == ""
    assert len(calls) == 2
