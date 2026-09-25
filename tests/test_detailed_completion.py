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
)


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
