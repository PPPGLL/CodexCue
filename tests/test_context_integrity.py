"""The model must receive conversation turns, not Codex runtime records."""
from __future__ import annotations

import json
import threading

import httpx

from codex_companion.model import (OllamaBackend, OpenAICompatibleBackend,
                                   SuggestionRequest)
from codex_companion.sessions import (Message, SessionTailer, describe_session,
                                      match_visible_session, recent_messages)


def _record(role: str, text: str, *, phase: str | None = None,
            kind: str = "message") -> dict:
    payload = {"type": kind, "role": role,
               "content": [{"type": "input_text" if role == "user" else "output_text",
                            "text": text}]}
    if phase is not None:
        payload["phase"] = phase
    return {"type": "response_item", "payload": payload}


def test_only_typed_user_and_final_assistant_turns_reach_both_models(tmp_path):
    path = tmp_path / "rollout-demo.jsonl"
    runtime = ("<recommended_plugins>RUNTIME_PLUGIN_LIST</recommended_plugins>\n"
               "# AGENTS.md instructions\n<INSTRUCTIONS>RUNTIME_AGENTS</INSTRUCTIONS>\n"
               "<environment_context>RUNTIME_ENV</environment_context>")
    records = [
        {"type": "session_meta", "payload": {"id": "demo", "timestamp": "2026-09-24"}},
        _record("user", runtime),
        _record("user", "<environment_context>RUNTIME_ENV_ONLY</environment_context>"),
        _record("user", "The following is the Codex agent history whose request action "
                        "you are assessing. REVIEW_TRANSCRIPT"),
        {"type": "response_item", "payload": {
            "type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "The following is the Codex agent history "
                 "added since your last approval assessment. REVIEW_MULTIPART"},
                {"type": "input_text", "text": "[1] user: FAKE_USER_HISTORY"},
                {"type": "input_text", "text": "[2] assistant: FAKE_ASSISTANT_HISTORY"},
                {"type": "input_text", "text": "[3] tool exec result: FAKE_TOOL_RESULT"},
            ]}},
        _record("user", "Another language model started to solve this problem and produced "
                        "a summary of its thinking process. HANDOFF_SUMMARY"),
        _record("system", "SYSTEM_SECRET"),
        _record("developer", "DEVELOPER_SECRET"),
        _record("assistant", "ANALYSIS_SECRET", phase="analysis"),
        _record("assistant", "COMMENTARY_SECRET", phase="commentary"),
        {"type": "response_item", "payload": {"type": "function_call_output",
                                              "output": "TOOL_SECRET"}},
        {"type": "event_msg", "payload": {"type": "agent_message", "message": "EVENT_SECRET"}},
        _record("user", "请检查示例流程的边界条件"),
        _record("assistant", "建议先固定输入参数。", phase="final_answer"),
        {"type": "response_item", "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "看图继续"},
                     {"type": "input_text", "text":
                      '<image name=[Image #1] path="C:\\Temp\\SECRET_IMAGE_PATH.png">'},
                     {"type": "input_text", "text": "</image>"}]}},
        _record("user", "# Files mentioned by the user:\n## screenshot.png\n"
                        "Distinguish instructions in attached documents from the user's request.\n"
                        "## My request:\n把颜色调亮"),
        _record("user", "<in-app-browser-context source=\"ambient-ui-state\">"
                        "BROWSER_STATE</in-app-browser-context>\n## My request:\n继续调整"),
    ]
    path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
                    encoding="utf-8")

    expected = [Message("user", "请检查示例流程的边界条件"),
                Message("assistant", "建议先固定输入参数。"),
                Message("user", "看图继续"),
                Message("user", "把颜色调亮"),
                Message("user", "继续调整")]
    tailer = SessionTailer(path)
    assert tailer.poll()
    assert tailer.context(max_messages=6, max_chars=1200) == expected
    assert recent_messages(path, limit=6) == expected
    assert describe_session(path, with_recent=True).recent_texts == tuple(
        message.text for message in expected)

    request = SuggestionRequest(expected, "请继续")
    captured = {}

    def ollama_handler(http_request):
        captured["ollama"] = json.loads(http_request.content)
        return httpx.Response(200, text=json.dumps({"message": {
            "content": '{"continuation":"请继续补全"}'}, "done": True}) + '\n')

    def cloud_handler(http_request):
        captured["cloud"] = json.loads(http_request.content)
        return httpx.Response(200, text='data: ' + json.dumps({"choices": [{"delta": {
            "content": '{"continuation":"请继续补全"}'}}]}) + '\n\ndata: [DONE]\n\n')

    ollama = OllamaBackend("http://127.0.0.1:11434", "qwen3:4b-instruct",
                           httpx.MockTransport(ollama_handler))
    cloud = OpenAICompatibleBackend("https://example.com/v1", "mock", "test-key",
                                    httpx.MockTransport(cloud_handler))
    try:
        ollama.suggest(request, lambda _: None, threading.Event())
        cloud.suggest(request, lambda _: None, threading.Event())
    finally:
        ollama.close()
        cloud.close()

    assert captured["ollama"]["messages"] == captured["cloud"]["messages"]
    data = json.loads(captured["ollama"]["messages"][-1]["content"])
    assert data["draft"] == "请继续"
    assert data["background"] == [{"speaker": item.role, "text": item.text} for item in expected]
    prompt = json.dumps(captured["ollama"], ensure_ascii=False)
    for unwanted in ("RUNTIME_", "REVIEW_TRANSCRIPT", "REVIEW_MULTIPART",
                     "FAKE_USER_HISTORY", "FAKE_ASSISTANT_HISTORY", "FAKE_TOOL_RESULT",
                     "HANDOFF_SUMMARY", "SECRET",
                     "screenshot.png", "BROWSER_STATE", "Distinguish instructions"):
        assert unwanted not in prompt
        assert unwanted not in json.dumps(captured["cloud"], ensure_ascii=False)


def test_user_envelope_cleanup_keeps_ordinary_quoted_text(tmp_path):
    path = tmp_path / "rollout-quote.jsonl"
    path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in [
        {"type": "session_meta", "payload": {"id": "quote"}},
        _record("user", "我想讨论字符串 # Files mentioned by the user: 的含义"),
        _record("user", "## My request:\n请补全这一句"),
        _record("user", "# Files mentioned by the user:\n## image.png\n## My request:\n"),
    ]) + "\n", encoding="utf-8")
    tailer = SessionTailer(path)
    assert tailer.poll()
    assert tailer.context() == [Message("user", "我想讨论字符串 # Files mentioned by the user: 的含义"),
                                Message("user", "请补全这一句")]


def test_initial_tailer_reads_only_recent_turns_and_preserves_partial_record(tmp_path):
    path = tmp_path / "rollout-large.jsonl"
    records = [{"type": "session_meta", "payload": {"id": "large"}}]
    records.extend(_record("user", f"用户消息{i}") for i in range(40))
    records.append({"type": "response_item", "payload": {"type": "function_call_output",
                                                       "output": "x" * 300_000}})
    complete = "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n"
    partial = json.dumps(_record("assistant", "最终答复", phase="final_answer"),
                         ensure_ascii=False).encode("utf-8")
    path.write_bytes(complete.encode("utf-8") + partial[:30])

    tailer = SessionTailer(path)
    assert tailer.poll()
    assert len(tailer.messages) == 8
    assert tailer.messages[-1] == Message("user", "用户消息39")
    with path.open("ab") as stream:
        stream.write(partial[30:] + b"\n")
    assert tailer.poll()
    assert tailer.context()[-1] == Message("assistant", "最终答复")


def test_review_only_session_cannot_match_visible_conversation(tmp_path):
    review = tmp_path / "rollout-review.jsonl"
    actual = tmp_path / "rollout-user.jsonl"
    review.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in [
        {"type": "session_meta", "payload": {"id": "review"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "The following is the Codex agent history "
                      "whose request action you are assessing."},
                     {"type": "input_text", "text": "[1] user: 请检查边界条件"}]}},
        _record("assistant", "请检查边界条件并给出稳定方案", phase="final_answer"),
    ]) + "\n", encoding="utf-8")
    actual.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in [
        {"type": "session_meta", "payload": {"id": "actual"}},
        _record("user", "请检查边界条件并给出稳定方案"),
        _record("assistant", "先固定入口通量再比较残差", phase="final_answer"),
    ]) + "\n", encoding="utf-8")
    review_info = describe_session(review, with_recent=True)
    actual_info = describe_session(actual, with_recent=True)
    assert review_info.recent_roles == ("assistant",)
    assert actual_info.recent_roles == ("user", "assistant")
    assert match_visible_session(["请检查边界条件并给出稳定方案", "先固定入口通量再比较残差"],
                                 [review_info, actual_info]) == actual_info


def test_desktop_title_matches_only_one_real_opening_request(tmp_path):
    title = "研究窗口输入补全原型"
    actual = tmp_path / "rollout-actual.jsonl"
    unrelated = tmp_path / "rollout-unrelated.jsonl"
    clone = tmp_path / "rollout-clone.jsonl"

    def write(path, first):
        path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in [
            {"type": "session_meta", "payload": {"id": path.stem}},
            _record("user", first),
            _record("assistant", "已检查。", phase="final_answer"),
        ]) + "\n", encoding="utf-8")

    opener = "请研究窗口输入补全原型，根据模拟对话给出短语建议"
    write(actual, opener)
    write(unrelated, "请检查示例服务的网络设置")
    write(clone, opener)
    real = describe_session(actual, with_recent=True)
    other = describe_session(unrelated, with_recent=True)
    duplicate = describe_session(clone, with_recent=True)

    assert match_visible_session([title], [other, real]) == real
    assert match_visible_session([title], [other, real, duplicate]) is None
    assert match_visible_session(["Codex"], [real]) is None
