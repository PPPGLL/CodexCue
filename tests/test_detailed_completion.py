import asyncio
import json
import threading

import httpx
import pytest

from codex_companion.model import (
    MAX_SUGGESTION_CHARS,
    OllamaBackend,
    OpenAICompatibleBackend,
    SuggestionRequest,
    decode_suggestion,
    draft_anchor,
    normalize_suggestion,
    repeats_input,
)
from codex_companion.sessions import Message


@pytest.mark.parametrize("cloud", [False, True])
def test_both_backends_preserve_a_complete_detailed_request(cloud):
    draft = "Please improve this form"
    suffix = (". Clarify the labels and group related fields together. "
              "Show which values need attention and keep the entered values when validation fails. "
              "Check that the form still submits successfully after correcting an invalid field.")
    assert 120 < len(suffix) < MAX_SUGGESTION_CHARS
    raw = json.dumps({"continuation": draft_anchor(draft) + suffix})

    def handler(request):
        body = json.loads(request.content)
        budget = body["max_tokens"] if cloud else body["options"]["num_predict"]
        assert budget >= 384  # JSON framing and copied anchor must also fit.
        pieces = [raw[:55], raw[55:130], raw[130:]]
        if cloud:
            text = "".join("data: " + json.dumps({"choices": [{"delta": {"content": part}}]})
                           + "\n\n" for part in pieces) + "data: [DONE]\n\n"
        else:
            text = "\n".join(json.dumps({"message": {"content": part}, "done": i == 2})
                             for i, part in enumerate(pieces))
        return httpx.Response(200, text=text)

    transport = httpx.MockTransport(handler)
    backend = (OpenAICompatibleBackend("https://example.com/v1", "fixture", "test-key", transport)
               if cloud else OllamaBackend("http://127.0.0.1:11434", "fixture", transport))
    emitted = []
    try:
        result = backend.suggest(SuggestionRequest([], draft), emitted.append, threading.Event())
        assert result == suffix
        assert emitted == [suffix]  # No JSON fragments or incomplete clauses.
    finally:
        backend.close()


def test_multiline_requirements_are_not_silently_lost():
    draft = "请调整表单"
    suffix = "。请把相关字段放在一起。\n输入校验失败时保留已填写的内容，方便修改后重试。"
    assert decode_suggestion(json.dumps({"continuation": draft + suffix}), draft) == suffix


def test_overlong_suggestion_ends_at_a_complete_requirement():
    complete = "。请先检查反馈信息是否清楚。"
    raw = complete + "再检查" * 200 + "。"
    assert normalize_suggestion(raw, "") == complete
    assert normalize_suggestion("unfinished " * 100, "") == ""
    assert normalize_suggestion("value 3.14 " * 100, "", limit=8) == ""


@pytest.mark.parametrize("cloud", [False, True])
@pytest.mark.parametrize("repeat_again", [False, True])
def test_copied_background_is_repaired_once_before_any_emission(cloud, repeat_again):
    draft = "这个图表不易读，改一下"
    copied = ("请在收到粗略修改意见后自动补充几个具体要求，描述检查方式和验收标准，"
              "避免重复已有内容，确保新增文字自然衔接原句。")
    corrected = "。请调整颜色对比和标注，使不同类别容易区分，并检查较小窗口下是否仍然清楚。"
    calls = []

    def handler(request):
        data = json.loads(json.loads(request.content)["messages"][-1]["content"])
        calls.append(data)
        assert data["draft"] == draft
        assert bool(data["background"]) == (len(calls) == 1)
        suffix = copied if len(calls) == 1 or repeat_again else corrected
        raw = json.dumps({"continuation": draft_anchor(draft) + suffix})
        if cloud:
            return httpx.Response(200, text="data: " + json.dumps({"choices": [{"delta": {"content": raw}}]})
                                  + "\n\ndata: [DONE]\n\n")
        return httpx.Response(200, text=json.dumps({"message": {"content": raw}, "done": True}))

    transport = httpx.MockTransport(handler)
    backend = (OpenAICompatibleBackend("https://example.com/v1", "fixture", "test-key", transport)
               if cloud else OllamaBackend("http://127.0.0.1:11434", "fixture", transport))
    emitted = []
    try:
        result = backend.suggest(SuggestionRequest([Message("assistant", copied)], draft),
                                 emitted.append, threading.Event())
        assert len(calls) == 2
        assert result == ("" if repeat_again else corrected)
        assert emitted == ([] if repeat_again else [corrected])
    finally:
        backend.close()


def test_shared_domain_terms_are_not_mistaken_for_copied_prose():
    request = SuggestionRequest([Message("user", "图表的颜色不容易分辨，标注也看不清楚。")], "调整一下图表")
    assert not repeats_input("。请提高颜色之间的对比度，让标注与对应数据保持清晰关联，再检查缩小窗口时是否仍可读。", request)


@pytest.mark.parametrize("cloud", [False, True])
def test_openers_do_not_turn_background_into_a_task(cloud):
    transport = httpx.MockTransport(lambda _: pytest.fail("An opener should wait for the subject"))
    backend = (OpenAICompatibleBackend("https://example.com/v1", "fixture", "test-key", transport)
               if cloud else OllamaBackend("http://127.0.0.1:11434", "fixture", transport))
    try:
        for draft in ("你好", "你好世界", "Hello", "测试", "我想", "我希望你", "我觉得", "现在有个问题"):
            assert backend.suggest(SuggestionRequest([Message("assistant", "Earlier instructions")], draft),
                                   lambda _: pytest.fail("No suggestion expected"), threading.Event()) == ""
    finally:
        backend.close()


@pytest.mark.parametrize("cloud", [False, True])
def test_editing_the_draft_cancels_a_stalled_repair(cloud):
    copied = "Earlier writing instructions must not be copied into the user's message. Return useful details instead."
    repair_started, interrupted, cancel = (threading.Event() for _ in range(3))
    calls = []

    async def handler(request):
        calls.append(True)
        if len(calls) == 2:
            repair_started.set()
            try:
                await asyncio.sleep(30)
            finally:
                interrupted.set()
        raw = json.dumps({"continuation": "draft" + copied})
        body = ("data: " + json.dumps({"choices": [{"delta": {"content": raw}}]}) + "\n\ndata: [DONE]\n\n"
                if cloud else json.dumps({"message": {"content": raw}, "done": True}))
        return httpx.Response(200, text=body)

    transport = httpx.MockTransport(handler)
    backend = (OpenAICompatibleBackend("https://example.com/v1", "fixture", "test-key", transport)
               if cloud else OllamaBackend("http://127.0.0.1:11434", "fixture", transport))
    results, emitted = [], []
    worker = threading.Thread(target=lambda: results.append(backend.suggest(
        SuggestionRequest([Message("assistant", copied)], "draft"), emitted.append, cancel)))
    try:
        worker.start()
        assert repair_started.wait(2)
        cancel.set()
        worker.join(1)
        assert not worker.is_alive() and interrupted.is_set()
        assert results == [""] and emitted == [] and len(calls) == 2
    finally:
        cancel.set()
        worker.join(2)
        backend.close()
