import asyncio
import json
import threading

import httpx
import pytest

from codex_companion.completion_modes import CompletionRoute, SuggestionText, parse_route
from codex_companion.config import AppConfig
from codex_companion.model import (OllamaBackend, OpenAICompatibleBackend, SuggestionRequest,
                                   complete_request, completion_background)
from codex_companion.sessions import Message


ITEMS = ["检查列表中的标题、正文与说明文字是否正确对齐，确认同类内容采用一致的间距，避免出现错位。",
         "核对长文本在窗口缩小时是否自然换行，检查是否存在文字截断、内容重叠或阅读顺序混乱的情况。",
         "保留当前信息与交互顺序，检查主次层级是否清楚，使用户能够快速找到并理解所需内容。"]


@pytest.mark.parametrize("kind", ["check", "optimize", "acceptance"])
def test_auto_routes_to_one_generator_and_keeps_metadata(kind):
    calls = []
    async def read(messages, schema, budget):
        calls.append((messages, schema, budget))
        if len(calls) == 1:
            return json.dumps({"mode": kind, "focus": "列表", "evidence": "调整列表显示"})
        return json.dumps({"requirements": ITEMS})
    result = asyncio.run(complete_request(read, SuggestionRequest([], "调整列表显示", "auto")))
    assert result.kind == kind and result.requirements == tuple(ITEMS)
    assert 80 <= len(result) <= 160 and len(calls) == 2
    from codex_companion.completion_modes import DETAIL_PROMPTS
    for candidate, prompt in DETAIL_PROMPTS.items():
        assert (prompt in calls[1][0][0]["content"]) == (kind == candidate)
    assert calls[1][1]["required"] == ["requirements"]


@pytest.mark.parametrize("draft,expected", [
    ("这个图表颜色分不清，调整一下", "check"),
    ("优化登录表单的间距", "check"),
    ("按钮点击后没有反馈，改一下", "optimize"),
    ("登录成功以后不知道下一步怎么操作", "optimize"),
    ("检查导出报告是否完整", "acceptance"),
    ("给页面布局定义验收标准", "acceptance"),
])
def test_clear_topics_stay_stable_across_model_proposals(draft, expected):
    from codex_companion.completion_modes import topic_kind
    for proposed in ("check", "optimize", "acceptance"):
        assert topic_kind(draft, proposed) == expected


@pytest.mark.parametrize("route", ["not json", '[]', '{"mode":"bogus"}',
    '{"mode":"check","focus":"列表","evidence":"unrelated history"}'])
def test_invalid_classification_still_generates_short_guess(route):
    calls = []
    async def read(messages, schema, budget):
        calls.append(schema)
        return route if len(calls) == 1 else json.dumps({"continuation": "我觉得这里还可以改进。"})
    result = asyncio.run(complete_request(read, SuggestionRequest([], "我觉得", "auto")))
    assert result == "这里还可以改进。" and result.kind == "short" and len(calls) == 2


@pytest.mark.parametrize("bad_items", [["太短", "不够具体"], ITEMS + ["要求五", "要求六"],
                                      [ITEMS[0], ITEMS[1], "必须在300ms内完成。"]])
def test_bad_details_repair_once_then_fall_back_to_short(bad_items):
    calls = []
    async def read(messages, schema, budget):
        calls.append(schema)
        if "mode" in schema["properties"]:
            return json.dumps({"mode": "check", "focus": "列表", "evidence": "调整列表"})
        if "requirements" in schema["properties"]:
            return json.dumps({"requirements": bad_items})
        return json.dumps({"continuation": "调整列表，让内容更容易阅读。"})
    result = asyncio.run(complete_request(read, SuggestionRequest([], "调整列表", "auto")))
    assert result.kind == "short" and result == "，让内容更容易阅读。" and len(calls) == 4


def test_short_only_skips_router_for_explicit_revision():
    async def read(messages, schema, budget):
        assert schema["required"] == ["continuation"]
        return json.dumps({"continuation": "调整列表，保持对齐。"})
    result = asyncio.run(complete_request(read, SuggestionRequest([], "调整列表", "short")))
    assert result.kind == "short" and result == "，保持对齐。"


def test_paraphrased_focus_uses_draft_but_invented_evidence_cannot_route():
    route = parse_route(json.dumps({"mode": "optimize", "focus": "交互反馈", "evidence": "按钮没有反馈"}),
                        "按钮没有反馈", [])
    assert route == CompletionRoute("optimize", "按钮没有反馈", "按钮没有反馈")


@pytest.mark.parametrize("evidence,expected", [("有什么问题", "optimize"), ("如何判定成功", "acceptance")])
def test_workflow_analysis_is_not_confused_with_result_acceptance(evidence, expected):
    draft = "登录流程" + evidence
    route = parse_route(json.dumps({"mode": "acceptance", "focus": "登录流程", "evidence": evidence}), draft, [])
    assert route.kind == expected


def test_analysis_only_constraints_reach_the_selected_generator():
    from codex_companion.model import build_detail_messages
    messages = build_detail_messages(SuggestionRequest([], "先不要动代码，看看结算流程"), CompletionRoute("optimize", "结算流程"))
    assert "当前请求只允许分析" in messages[0]["content"]
    assert "暂不修改现有行为" in messages[2]["content"]


def test_short_history_echo_retries_without_hiding_the_opener():
    calls = []
    async def read(messages, schema, budget):
        data = json.loads(messages[-1]["content"])
        calls.append(data)
        suffix = "刚才那段生成结果不合适。" if len(calls) == 1 else "这里还可以更简洁。"
        return json.dumps({"continuation": data["anchor"] + suffix})
    result = asyncio.run(complete_request(read, SuggestionRequest([Message("user", "刚才那段生成结果不合适。")], "我觉得")))
    assert result == "这里还可以更简洁。" and len(calls) == 2 and calls[1]["background"] == []


@pytest.mark.parametrize("cloud", [False, True])
def test_route_and_generation_share_one_total_deadline(cloud):
    calls = []
    async def handler(request):
        calls.append(True)
        await asyncio.sleep(.08)
        raw = json.dumps({"mode": "check", "focus": "列表", "evidence": "调整列表"}
                         if len(calls) == 1 else {"requirements": ITEMS})
        body = ("data: " + json.dumps({"choices": [{"delta": {"content": raw}}]}) + "\n\ndata: [DONE]\n\n"
                if cloud else json.dumps({"message": {"content": raw}, "done": True}))
        return httpx.Response(200, text=body)
    transport = httpx.MockTransport(handler)
    backend = (OpenAICompatibleBackend("https://example.com/v1", "fixture", "key", transport, request_timeout=.13)
               if cloud else OllamaBackend("http://127.0.0.1:11434", "fixture", transport, request_timeout=.13))
    try:
        with pytest.raises(TimeoutError):
            backend.suggest(SuggestionRequest([], "调整列表", "auto"), lambda _: pytest.fail("late result"), threading.Event())
        assert len(calls) == 2
    finally:
        backend.close()


def test_quoted_generation_policy_is_removed_without_losing_real_topic():
    background = [Message("assistant", "补全偶尔不显示，设置窗口也无法打开。\n收到粗略修改意见后补充具体要求，控制在80到160个中文字。"),
                  Message("user", "检查2个按钮的布局。")] 
    filtered = completion_background(background)
    assert [m.text for m in filtered] == ["补全偶尔不显示，设置窗口也无法打开。", "检查2个按钮的布局。"]
    assert "80到160" in background[0].text


def test_config_migrates_and_persists_style_independently(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"completion_mode":"draft"}', encoding="utf-8")
    config = AppConfig.load(path)
    assert config.completion_style == "auto" and config.completion_mode == "draft"
    config.completion_style = "short"
    config.save(path)
    assert AppConfig.load(path).completion_style == "short"
    path.write_text('{"completion_style":"wrong"}', encoding="utf-8")
    assert AppConfig.load(path).recovery_required


def test_settings_save_style_without_changing_context(qapp, monkeypatch):
    from codex_companion.app import SettingsDialog
    saved = []
    monkeypatch.setattr(AppConfig, "save", lambda self: saved.append(self))
    config = AppConfig(completion_mode="draft")
    dialog = SettingsDialog(config)
    try:
        assert dialog.completion_style.currentData() == "auto"
        dialog.completion_style.setCurrentIndex(dialog.completion_style.findData("short"))
        dialog._commit_config("ollama")
        assert saved[0].completion_style == config.completion_style == "short"
        assert saved[0].completion_mode == config.completion_mode == "draft"
    finally:
        dialog.close()


def test_late_detailed_result_cannot_replace_new_short_metadata():
    from codex_companion.state import SuggestionState
    state = SuggestionState()
    state.observe("调整列表", 0, 0)
    old = state.start()
    state.observe("我觉得", 0, 1)
    current = state.start()
    assert state.finish(current, SuggestionText("这里还可以改进。"))
    assert not state.finish(old, SuggestionText("。" + "".join(ITEMS), "check", tuple(ITEMS)))
    assert state.suggestion.kind == "short"


@pytest.mark.parametrize("cloud", [False, True])
@pytest.mark.parametrize("stage", [1, 2])
def test_cancel_during_route_or_details_never_emits(cloud, stage):
    reached, interrupted, cancel = (threading.Event() for _ in range(3))
    calls, results, emitted = [], [], []
    async def handler(request):
        calls.append(True)
        if len(calls) == stage:
            reached.set()
            try:
                await asyncio.sleep(30)
            finally:
                interrupted.set()
        raw = json.dumps({"mode": "check", "focus": "列表", "evidence": "调整列表"})
        body = ("data: " + json.dumps({"choices": [{"delta": {"content": raw}}]}) + "\n\ndata: [DONE]\n\n"
                if cloud else json.dumps({"message": {"content": raw}, "done": True}))
        return httpx.Response(200, text=body)
    transport = httpx.MockTransport(handler)
    backend = (OpenAICompatibleBackend("https://example.com/v1", "fixture", "key", transport)
               if cloud else OllamaBackend("http://127.0.0.1:11434", "fixture", transport))
    thread = threading.Thread(target=lambda: results.append(backend.suggest(
        SuggestionRequest([], "调整列表", "auto"), emitted.append, cancel)))
    try:
        thread.start()
        assert reached.wait(2)
        cancel.set()
        thread.join(1)
        assert not thread.is_alive() and interrupted.is_set()
        assert results == [""] and emitted == [] and len(calls) == stage
    finally:
        cancel.set()
        thread.join(2)
        backend.close()


def test_queued_qt_signal_preserves_request_metadata_and_popup_badge(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from codex_companion.app import Bridge, SuggestionPopup
    from codex_companion.i18n import tr
    bridge = Bridge()
    delivered = []
    bridge.finished.connect(lambda token, text: delivered.append((token, text)), Qt.QueuedConnection)
    result = SuggestionText("。检查内容。", "check", ("检查内容。",))
    thread = threading.Thread(target=lambda: bridge.finished.emit("token", result))
    thread.start()
    thread.join(1)
    QTest.qWait(30)
    assert delivered == [("token", result)] and delivered[0][1].kind == "check"
    popup = SuggestionPopup()
    try:
        popup.show_text(result, (10, 10, 500, 100), suggest=True, kind=result.kind)
        assert popup.label.text() == result and popup.scope_hint.text() == tr("kind_check")
    finally:
        popup.close()
